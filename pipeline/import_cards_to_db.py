#!/usr/bin/env python3
"""
Импорт готовых карточек персон (MD/текстовые файлы) в БД synthetic_v2/db/personas.sqlite.
Без LLM: читает файлы, вычисляет title и persona_id, сохраняет profile_md целиком, обновляет FTS.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB_PATH_DEFAULT = ROOT / "db" / "personas.sqlite"


def slugify(text: str, max_len: int = 60) -> str:
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_len].strip("-_")


def ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
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
            CREATE TABLE IF NOT EXISTS persona_tags (
                persona_id TEXT,
                category TEXT,
                value TEXT,
                PRIMARY KEY (persona_id, category, value)
            )
            """
        )
        # FTS индекс
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS personas_fts USING fts5(
                persona_id,
                title,
                profile_md
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_persona(db_path: Path, persona_id: str, title: str, profile_md: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
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
    finally:
        conn.close()


def infer_title(profile_md: str, fallback: str) -> str:
    for line in profile_md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            s = s.lstrip("#").strip()
        return s[:160]
    return fallback


def walk_files(root: Path, glob: str) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for p in sorted(root.glob(glob)):
        if p.is_file():
            yield p


def compute_persona_id(file_stem: str, content: str) -> str:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    base = f"{file_stem}:{content_hash}"
    pid = "p_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
    return pid


def main():
    ap = argparse.ArgumentParser(description="Импорт готовых карточек (MD/текст) в БД персон.")
    ap.add_argument("--cards-dir", type=Path, required=True, help="Папка или файл карточки")
    ap.add_argument("--glob", type=str, default="*", help="Маска файлов (по умолчанию все)")
    ap.add_argument("--db-path", type=Path, default=DB_PATH_DEFAULT, help="Путь к SQLite (personas.sqlite)")
    args = ap.parse_args()

    cards_dir = args.cards_dir.resolve()
    if not cards_dir.exists():
        raise SystemExit(f"Не найден путь: {cards_dir}")

    ensure_db(args.db_path)

    imported = 0
    for path in walk_files(cards_dir, args.glob):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            # попробуем бинарные/другие кодировки пропустить
            continue
        if not text:
            continue
        title = infer_title(text, fallback=path.stem)
        persona_id = compute_persona_id(path.stem, text)
        upsert_persona(args.db_path, persona_id, title, text)
        imported += 1
        print(f"OK: {persona_id} — {title}")

    print(f"Импорт завершён. Записано карточек: {imported}. БД: {args.db_path}")


if __name__ == "__main__":
    main()

