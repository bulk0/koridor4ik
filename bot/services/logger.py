from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
# Разрешаем переопределить путь к логам через переменную окружения (для PaaS, где /app может быть read-only)
import os
_runs_root = Path(os.getenv("RUNS_DIR", str(ROOT / "runs")))
RUNS_DIR = _runs_root / "chats" / "bot"
try:
	RUNS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
	# Фолбэк в /tmp если нет прав записи
	RUNS_DIR = Path("/tmp/synthetic_runs/chats/bot")
	RUNS_DIR.mkdir(parents=True, exist_ok=True)

def now_ts() -> str:
	return datetime.now().strftime("%Y%m%d_%H%M%S")

def log_event(user_id: int, session_id: str, op: str, **fields: Any) -> None:
	record = {
		"ts": datetime.utcnow().isoformat() + "Z",
		"user_id": user_id,
		"session_id": session_id,
		"op": op,
		**fields,
	}
	log_path = RUNS_DIR / "events.jsonl"
	with log_path.open("a", encoding="utf-8") as f:
		f.write(json.dumps(record, ensure_ascii=False) + "\n")

@dataclass
class SessionFiles:
	user_dir: Path
	session_dir: Path
	summary_md: Path

def ensure_session_files(user_id: int, session_id: Optional[str] = None) -> SessionFiles:
	user_dir = RUNS_DIR / f"u_{user_id}"
	user_dir.mkdir(parents=True, exist_ok=True)
	sid = session_id or now_ts()
	session_dir = user_dir / f"s_{sid}"
	session_dir.mkdir(parents=True, exist_ok=True)
	summary_md = session_dir / f"summary_{sid}.md"
	if not summary_md.exists():
		summary_md.write_text("# Диалог с несколькими персонами (бот)\n\n", encoding="utf-8")
	return SessionFiles(user_dir=user_dir, session_dir=session_dir, summary_md=summary_md)

def append_question(session: SessionFiles, question: str) -> None:
	with session.summary_md.open("a", encoding="utf-8") as f:
		f.write(f"## Вопрос\n\n{question}\n\n")

def append_answer(session: SessionFiles, persona_title: str, answer: str) -> None:
	with session.summary_md.open("a", encoding="utf-8") as f:
		f.write(f"### Персона: {persona_title}\n\n{answer}\n\n---\n")

def export_answers_file(session: SessionFiles, question: str, answers: List[Dict[str, str]]) -> Path:
	ts = now_ts()
	out = session.session_dir / f"answers_{ts}.md"
	lines: List[str] = [f"# Ответы на вопрос\n\n## Вопрос\n\n{question}\n\n---\n"]
	for item in answers:
		lines.append(f"## Персона: {item['title']}\n\n{item['answer']}\n\n---\n")
	out.write_text("\n".join(lines), encoding="utf-8")
	return out


