#!/usr/bin/env python3
"""
manage_personas.py — интерактивный менеджер персон (импорт/генерация/теги)

Что умеет:
- Импорт новой партии расшифровок → (если нужно) конвертация в .txt → генерация карточек LLM → запись в БД → (опц.) теги
- Импорт готовых карточек (MD/текст) в БД без LLM
- Проставление тегов по CSV/JSON
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "personas.sqlite"


def ensure_env():
    if load_dotenv is not None:
        load_dotenv(dotenv_path=ROOT / ".env", override=True)
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")):
        print("Не найден API ключ (ANTHROPIC_API_KEY/OPENAI_API_KEY). Добавьте в synthetic_v2/.env.")


def all_files_txt(d: Path) -> bool:
    files = [p for p in d.iterdir() if p.is_file()]
    return bool(files) and all(p.suffix.lower() == ".txt" for p in files)


def pipeline_ingest_generate_tag():
    print("\n— Импорт новой партии расшифровок → генерация персон → теги —")
    src = input("Путь к папке с расшифровками (можно .md/.docx/.txt): ").strip()
    if not src:
        print("Отмена.")
        return
    src_dir = Path(src).expanduser().resolve()
    if not src_dir.exists() or not src_dir.is_dir():
        print("Папка не найдена.")
        return

    # 1) Проверка/конвертация
    if all_files_txt(src_dir):
        txt_dir = src_dir
        print("Все файлы уже .txt — конвертация не требуется.")
    else:
        ts_txt = datetime.now().strftime("v%Y%m%d_txt")
        txt_dir = ROOT / "data" / "personas" / ts_txt
        txt_dir.mkdir(parents=True, exist_ok=True)
        print(f"Конвертация в .txt → {txt_dir}")
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "pipeline" / "ingest_transcripts.py"),
                "--in-dir",
                str(src_dir),
                "--out-dir",
                str(txt_dir),
            ],
            check=True,
            env=os.environ.copy(),
        )

    # 2) Генерация карточек + запись в БД
    ts_gen = datetime.now().strftime("v%Y%m%d_generated")
    gen_dir = ROOT / "data" / "personas" / ts_gen
    gen_dir.mkdir(parents=True, exist_ok=True)
    print(f"Генерация персон в {gen_dir} и запись в БД {DB_PATH}")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "pipeline" / "generate_personas_from_transcripts.py"),
            "--txt-dir",
            str(txt_dir),
            "--out-dir",
            str(gen_dir),
            "--db-path",
            str(DB_PATH),
        ],
        check=True,
        env=os.environ.copy(),
    )

    # 3) Теги (опц.)
    tags_path = input("Путь к CSV/JSON с тегами (persona_id,category,value) или ENTER, чтобы пропустить: ").strip()
    if tags_path:
        tags_file = Path(tags_path).expanduser().resolve()
        if tags_file.exists():
            print("Проставление тегов...")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "tag_personas.py"),
                    "--db-path",
                    str(DB_PATH),
                    "--input",
                    str(tags_file),
                ],
                check=True,
                env=os.environ.copy(),
            )
        else:
            print("Файл с тегами не найден — шаг пропущен.")

    print("\nИмпорт завершён.")
    print(f"- TXT: {txt_dir}")
    print(f"- Карточки: {gen_dir}")
    print(f"- БД: {DB_PATH}")


def import_ready_cards():
    print("\n— Импорт готовых карточек (MD/текст) → БД —")
    cards = input("Путь к папке или файлу карточек: ").strip()
    if not cards:
        print("Отмена.")
        return
    cards_path = Path(cards).expanduser().resolve()
    if not cards_path.exists():
        print("Путь не найден.")
        return
    glob = input("Маска файлов (ENTER = *): ").strip() or "*"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "pipeline" / "import_cards_to_db.py"),
            "--cards-dir",
            str(cards_path),
            "--glob",
            glob,
            "--db-path",
            str(DB_PATH),
        ],
        check=True,
        env=os.environ.copy(),
    )


def tag_from_file():
    print("\n— Проставление тегов —")
    tags = input("Путь к CSV/JSON (persona_id,category,value): ").strip()
    if not tags:
        print("Отмена.")
        return
    path = Path(tags).expanduser().resolve()
    if not path.exists():
        print("Файл не найден.")
        return
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "pipeline" / "tag_personas.py"),
            "--db-path",
            str(DB_PATH),
            "--input",
            str(path),
        ],
        check=True,
        env=os.environ.copy(),
    )


def main():
    ensure_env()
    while True:
        print("\nManage Personas:")
        print("1) Импорт расшифровок → .txt → генерация → БД → (опц.) теги")
        print("2) Импорт готовых карточек (MD/текст) в БД")
        print("3) Проставить теги из CSV/JSON")
        print("0) Выход")
        choice = input("Выбор: ").strip()
        if choice == "1":
            pipeline_ingest_generate_tag()
        elif choice == "2":
            import_ready_cards()
        elif choice == "3":
            tag_from_file()
        elif choice == "0":
            break
        else:
            print("Неверный выбор.")


if __name__ == "__main__":
    main()

