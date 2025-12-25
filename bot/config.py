from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
	from dotenv import load_dotenv  # type: ignore
except Exception:
	def load_dotenv(*args, **kwargs):  # type: ignore
		return False

ROOT = Path(__file__).resolve().parents[1]

def _load_env() -> None:
	load_dotenv(dotenv_path=ROOT / ".env", override=True)

@dataclass(frozen=True)
class BotConfig:
	bot_token: str
	mode: str  # "webhook" | "polling"
	webhook_base_url: Optional[str]
	webhook_secret_path: Optional[str]
	personas_db_path: Path
	llm_provider: Optional[str]
	llm_model: Optional[str]
	llm_max_concurrency: int
	llm_timeout_s: float

	@staticmethod
	def from_env() -> "BotConfig":
		_load_env()
		mode = os.getenv("TELEGRAM_MODE", "polling").lower()
		personas_db_path = Path(os.getenv("PERSONAS_DB_PATH", str(ROOT / "db" / "personas.sqlite")))
		return BotConfig(
			bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
			mode=mode,
			webhook_base_url=os.getenv("WEBHOOK_BASE_URL"),
			webhook_secret_path=os.getenv("WEBHOOK_SECRET_PATH"),
			personas_db_path=personas_db_path,
			llm_provider=os.getenv("LLM_PROVIDER"),
			llm_model=os.getenv("LLM_MODEL"),
			llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "4")),
			llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "120")),
		)

	def validate(self) -> None:
		if not self.bot_token:
			raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
		if not self.personas_db_path.exists():
			raise RuntimeError(f"БД персон не найдена: {self.personas_db_path}")
		if self.mode == "webhook":
			if not (self.webhook_base_url and self.webhook_secret_path):
				raise RuntimeError("Для режима webhook задайте WEBHOOK_BASE_URL и WEBHOOK_SECRET_PATH")


