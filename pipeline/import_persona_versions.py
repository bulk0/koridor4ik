#!/usr/bin/env python3
"""
import_persona_versions.py — импорт новых версий карточек из папок и пометка старых как неактивных (журнал).

Особенности:
- Ищет .md файлы в переданных директориях (как на верхнем уровне, так и в подкаталоге cards_md/).
- Пытается извлечь persona_id из имени файла по шаблону *_p_<hex>.md. Если не удалось — пропускает файл.
- Обновляет таблицы:
  - personas (REPLACE)
  - personas_fts (REPLACE)
  - persona_versions_log: журнал версий с историей (PRIMARY KEY (persona_id, created_at))
    * Все предыдущие версии для persona_id помечаются active=0
    * Новая версия добавляется с active=1

Пример:
  python synthetic_v2/tools/import_persona_versions.py \\
    --db-path synthetic_v2/db/personas.sqlite \\
    --dir synthetic_v2/data/personas/v20251218_auto_generated_done \\
    --dir synthetic_v2/data/personas/v20251218_frozen1_generated
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, List


def ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS personas (
            persona_id TEXT PRIMARY KEY,
            title TEXT,
            profile_md TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS personas_fts USING fts5(
            persona_id,
            title,
            profile_md
        )
        """
    )
    # Журнал версий с историей
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS persona_versions_log (
            persona_id TEXT,
            title TEXT,
            profile_md TEXT,
            created_at TEXT,
            source_path TEXT,
            active INTEGER DEFAULT 1,
            PRIMARY KEY (persona_id, created_at)
        )
        """
    )
    conn.commit()


def extract_persona_id_from_filename(name: str) -> str | None:
    # Ожидаем шаблон ..._p_<12+ hex>.md
    m = re.search(r"_p_([0-9a-fA-F]{8,})\.md$", name)
    if not m:
        return None
    return f"p_{m.group(1).lower()}"


def walk_md_files(root: Path) -> Iterable[Path]:
    if root.is_file() and root.suffix.lower() == ".md":
        yield root
        return
    # Вариант 1: файлы сразу в папке
    for p in sorted(root.glob("*.md")):
        if p.is_file():
            yield p
    # Вариант 2: подкаталог cards_md/
    cards = root / "cards_md"
    if cards.exists() and cards.is_dir():
        for p in sorted(cards.glob("*.md")):
            if p.is_file():
                yield p


def infer_title(profile_md: str, fallback: str) -> str:
    for line in profile_md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            s = s.lstrip("#").strip()
        return s[:160]
    return fallback


def upsert_persona(conn: sqlite3.Connection, persona_id: str, title: str, profile_md: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO personas(persona_id, title, profile_md, created_at) VALUES (?, ?, ?, ?)",
        (persona_id, title, profile_md, datetime.now().isoformat(timespec="seconds")),
    )
    # FTS replace
    cur.execute("DELETE FROM personas_fts WHERE persona_id=?", (persona_id,))
    cur.execute(
        "INSERT INTO personas_fts(persona_id, title, profile_md) VALUES (?, ?, ?)",
        (persona_id, title, profile_md),
    )
    conn.commit()


def log_new_version(conn: sqlite3.Connection, persona_id: str, title: str, profile_md: str, source_path: str) -> None:
    cur = conn.cursor()
    # Снимаем активность со всех прежних
    cur.execute("UPDATE persona_versions_log SET active=0 WHERE persona_id=?", (persona_id,))
    # Добавляем новую активную
    cur.execute(
        "INSERT OR REPLACE INTO persona_versions_log(persona_id, title, profile_md, created_at, source_path, active) VALUES (?,?,?,?,?,1)",
        (persona_id, title, profile_md, datetime.now().isoformat(timespec="seconds"), source_path),
    )
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Импорт новых версий карточек и деактивация старых (журнал версий).")
    ap.add_argument("--db-path", type=Path, required=True, help="Путь к SQLite (personas.sqlite)")
    ap.add_argument("--dir", type=Path, action="append", required=True, help="Папка с карточками (можно несколько)")
    args = ap.parse_args()

    conn = sqlite3.connect(str(args.db_path))
    try:
        ensure_tables(conn)
        imported = 0
        skipped = 0
        for d in args.dir:
            root = d.resolve()
            if not root.exists():
                continue
            for path in walk_md_files(root):
                try:
                    pid = extract_persona_id_from_filename(path.name)
                    if not pid:
                        skipped += 1
                        continue
                    md = path.read_text(encoding="utf-8").strip()
                    if not md:
                        skipped += 1
                        continue
                    title = infer_title(md, fallback=path.stem)
                    upsert_persona(conn, pid, title, md)
                    log_new_version(conn, pid, title, md, str(path))
                    imported += 1
                    print(f"IMPORTED: {pid} — {title}")
                except Exception as e:
                    skipped += 1
                    print(f"[SKIP] {path}: {e}")
        print(f"Done. imported={imported} skipped={skipped} db={args.db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()


