#!/usr/bin/env python3
"""
Freeze & dedupe:
- Ensures tables sources and persona_versions exist.
- Marks transcripts in --done-transcripts (compute SHA) into sources to prevent re-generation.
- Reads canonical personas from --done-cards (filenames ending with _p_<id>.md) and records them as active versions.
- Moves other personas created on --day (YYYY-MM-DD) that are NOT in canonical set into persona_versions (active=0),
  removes them from main tables (personas, personas_fts, persona_tags).
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path
from datetime import datetime
import hashlib

ROOT = Path(__file__).resolve().parents[1]
DB_PATH_DEFAULT = ROOT / "db" / "personas.sqlite"


def ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            content_sha TEXT PRIMARY KEY,
            source_path TEXT,
            last_persona_id TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS persona_versions (
            persona_id TEXT PRIMARY KEY,
            title TEXT,
            profile_md TEXT,
            created_at TEXT,
            source_sha TEXT,
            active INTEGER DEFAULT 1
        )
        """
    )
    conn.commit()


PID_RE = re.compile(r"_p_([0-9a-fA-F]+)\.md$")


def extract_persona_id_from_filename(p: Path) -> str | None:
    m = PID_RE.search(p.name)
    return f"p_{m.group(1)}" if m else None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def upsert_source(conn: sqlite3.Connection, sha: str, src_path: str, persona_id: str | None) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sources(content_sha, source_path, last_persona_id, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(content_sha) DO UPDATE SET "
        "source_path=excluded.source_path, "
        "last_persona_id=COALESCE(excluded.last_persona_id, sources.last_persona_id), "
        "updated_at=excluded.updated_at",
        (sha, src_path, persona_id, datetime.now().isoformat(timespec="seconds")),
    )


def upsert_version(conn: sqlite3.Connection, pid: str, sha: str | None, active: int) -> None:
    cur = conn.cursor()
    row = cur.execute("SELECT title, profile_md, created_at FROM personas WHERE persona_id=?", (pid,)).fetchone()
    if not row:
        return
    title, profile_md, created_at = row
    cur.execute(
        "INSERT INTO persona_versions(persona_id, title, profile_md, created_at, source_sha, active) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(persona_id) DO UPDATE SET title=excluded.title, profile_md=excluded.profile_md, created_at=excluded.created_at, source_sha=COALESCE(excluded.source_sha, persona_versions.source_sha), active=excluded.active",
        (pid, title, profile_md, created_at, sha, active),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-path", type=Path, default=DB_PATH_DEFAULT)
    ap.add_argument("--done-cards", type=Path, required=True, help="Folder with canonical *_p_<id>.md files")
    ap.add_argument("--done-transcripts", type=Path, required=True, help="Folder with source *.txt that must be frozen")
    ap.add_argument("--day", type=str, required=True, help="YYYY-MM-DD; only dedupe personas created on this date")
    args = ap.parse_args()

    conn = sqlite3.connect(str(args.db_path))
    try:
        ensure_tables(conn)
        cur = conn.cursor()

        # 1) Mark done transcripts in sources to prevent re-generation
        if args.done_transcripts.exists():
            for tp in sorted(p for p in args.done_transcripts.glob("*.txt")):
                txt = tp.read_text(encoding="utf-8")
                sha = sha256_text(txt)
                upsert_source(conn, sha, str(tp), None)
            conn.commit()

        # 2) Read canonical persona ids from cards
        canonical_ids: set[str] = set()
        for cp in sorted(p for p in args.done_cards.glob("*.md")):
            pid = extract_persona_id_from_filename(cp)
            if pid:
                canonical_ids.add(pid)

        # 3) Ensure canonical versions exist and are marked active
        for pid in canonical_ids:
            upsert_version(conn, pid, None, 1)
        conn.commit()

        # 4) Move today's non-canonical personas to versions and delete from main tables
        rows = cur.execute(
            "SELECT persona_id FROM personas WHERE substr(created_at,1,10)=?",
            (args.day,),
        ).fetchall()
        moved, kept = 0, 0
        for (pid,) in rows:
            if pid in canonical_ids:
                kept += 1
                continue
            # mark version inactive and remove from main tables
            upsert_version(conn, pid, None, 0)
            cur.execute("DELETE FROM persona_tags WHERE persona_id=?", (pid,))
            cur.execute("DELETE FROM personas_fts WHERE persona_id=?", (pid,))
            cur.execute("DELETE FROM personas WHERE persona_id=?", (pid,))
            moved += 1
        conn.commit()

        print(f"Canonical personas kept: {kept}")
        print(f"Moved to persona_versions (inactive): {moved}")
        print("Freeze/dedupe complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

