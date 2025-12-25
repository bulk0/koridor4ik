#!/usr/bin/env python3
"""
Проставляет теги персонам в SQLite (v2).
Формат входа:
  - CSV: persona_id, category, value
  - JSON: [{"persona_id":"...", "category":"age", "value":"25-34"}, ...]
"""
import argparse
import csv
import json
import sqlite3
from pathlib import Path

def ensure_tables(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS persona_tags (persona_id TEXT, category TEXT, value TEXT, PRIMARY KEY (persona_id, category, value))"
        )
        conn.commit()
    finally:
        conn.close()

def upsert_tags(db_path: Path, triples: list[tuple[str, str, str]]) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        for pid, category, value in triples:
            cur.execute(
                "INSERT OR IGNORE INTO persona_tags(persona_id, category, value) VALUES (?, ?, ?)",
                (pid, category, value),
            )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()

def read_triples(path: Path) -> list[tuple[str, str, str]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [
                (str(x["persona_id"]), str(x["category"]), str(x["value"]))
                for x in data
                if "persona_id" in x and "category" in x and "value" in x
            ]
        raise ValueError("JSON должен быть списком объектов {persona_id, category, value}")
    triples: list[tuple[str, str, str]] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = str(row.get("persona_id", "")).strip()
            category = str(row.get("category", "")).strip()
            value = str(row.get("value", "")).strip()
            if pid and category and value:
                triples.append((pid, category, value))
    return triples

def main():
    ap = argparse.ArgumentParser(description="Проставить теги персонам")
    ap.add_argument("--db-path", type=Path, required=True, help="Путь к SQLite (personas.sqlite)")
    ap.add_argument("--input", type=Path, required=True, help="CSV или JSON с полями persona_id, category, value")
    args = ap.parse_args()

    ensure_tables(args.db_path)
    triples = read_triples(args.input)
    n = upsert_tags(args.db_path, triples)
    print(f"tags_upserted={n}")

if __name__ == "__main__":
    main()

