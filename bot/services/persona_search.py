from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from chat.talk import (
	search_by_description_with_fallback,
	list_all_tags,
	search_personas_advanced,
	format_tags_line,
	Persona,
)
from .async_llm import AsyncLLMClient

@dataclass
class TTLCacheEntry:
	expire_at: float
	value: object

class TTLCache:
	def __init__(self) -> None:
		self._store: Dict[str, TTLCacheEntry] = {}
		self._lock = asyncio.Lock()

	async def get(self, key: str) -> Optional[object]:
		async with self._lock:
			entry = self._store.get(key)
			if not entry:
				return None
			if entry.expire_at < time.time():
				self._store.pop(key, None)
				return None
			return entry.value

	async def set(self, key: str, value: object, ttl_s: float) -> None:
		async with self._lock:
			self._store[key] = TTLCacheEntry(expire_at=time.time() + ttl_s, value=value)

class PersonaSearchService:
	def __init__(self) -> None:
		self._cache = TTLCache()

	async def search_by_description(self, query: str, llm: AsyncLLMClient, k_fts: int = 50, top_k: int = 15) -> List[Persona]:
		key = f"nl:{query.strip().lower()}:{k_fts}:{top_k}"
		cached = await self._cache.get(key)
		if isinstance(cached, list):
			return cached  # type: ignore[return-value]
		# Выполняем синхронную функцию в пуле потоков, чтобы не блокировать event loop
		def _run() -> List[Persona]:
			# используем внутренний sync LLM (создаётся внутри вызова) — сам поиск делает свои вызовы
			# мы не указываем max_tokens согласно правилам проекта
			return search_by_description_with_fallback(query, llm._client, k_fts=k_fts, top_k=top_k)  # type: ignore[attr-defined]
		personas: List[Persona] = await asyncio.to_thread(_run)
		await self._cache.set(key, personas, ttl_s=1800.0)
		return personas

	async def search_by_filters(self, include_all: Dict[str, List[str]], include_any: Dict[str, List[str]], exclude: Dict[str, List[str]], title_like: Optional[str], limit: int = 500) -> List[Persona]:
		def _run() -> List[Persona]:
			return search_personas_advanced(include_all, include_any, exclude, title_like, limit=limit)
		return await asyncio.to_thread(_run)

	async def tags_catalog(self) -> Dict[str, List[Tuple[str, int]]]:
		key = "tags_catalog"
		cached = await self._cache.get(key)
		if isinstance(cached, dict):
			return cached  # type: ignore[return-value]
		def _run() -> Dict[str, List[Tuple[str, int]]]:
			return list_all_tags()
		data = await asyncio.to_thread(_run)
		await self._cache.set(key, data, ttl_s=3600.0)
		return data

	def compact_tags(self, persona_id: str, max_len: int = 140) -> str:
		return format_tags_line(persona_id, max_len=max_len)


