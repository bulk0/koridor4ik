from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from chat.llm_client import LLMClient

@dataclass
class AsyncLLMClient:
	max_concurrency: int = 4
	timeout_s: float = 120.0

	def __post_init__(self) -> None:
		self._client = LLMClient()
		self._semaphore = asyncio.Semaphore(self.max_concurrency)

	async def chat(self, system: str, user: str, *, temperature: float = 0.25, max_tokens: Optional[int] = None) -> str:
		async with self._semaphore:
			return await asyncio.wait_for(
				asyncio.to_thread(self._client.chat, system, user, temperature, max_tokens),
				timeout=self.timeout_s,
			)

	async def chat_with_meta(self, system: str, user: str, *, temperature: float = 0.25, max_tokens: Optional[int] = None) -> Tuple[str, Optional[str]]:
		async with self._semaphore:
			return await asyncio.wait_for(
				asyncio.to_thread(self._client.chat_with_meta, system, user, temperature=temperature, max_tokens=max_tokens),
				timeout=self.timeout_s,
			)

	async def preflight_check(self) -> Dict[str, Any]:
		# дешёвый вызов, также защищён семафором/таймаутом
		async with self._semaphore:
			return await asyncio.wait_for(
				asyncio.to_thread(self._client.preflight_check),
				timeout=self.timeout_s,
			)


