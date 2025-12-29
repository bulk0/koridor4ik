from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

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

	# ---------- Быстрые асинхронные версии шагов поиска ----------
	async def fts_candidates(self, query: str, k: int = 50) -> List[Persona]:
		from chat.talk import fts_candidates as fts_sync
		return await asyncio.to_thread(fts_sync, query, k)

	async def _llm_map_description_to_filters_async(self, llm: AsyncLLMClient, query: str) -> Dict[str, Any]:
		# готовим известную таксономию и prompt (повтор промпта из talk.llm_map_description_to_filters)
		from chat.talk import db_taxonomy
		import json
		known = await asyncio.to_thread(db_taxonomy)
		tax_cat_list = sorted(known.keys())
		system = (
			"Ты — помощник по поиску персон в каталоге. "
			"Верни СТРОГО валидный JSON без пояснений и без форматирования Markdown."
		)
		user = (
			"Пользователь описал целевую персону естественным языком. "
			"Сопоставь это описание известной таксономии и ключевым словам для поиска.\n\n"
			f"Описание: \"\"\"{query.strip()}\"\"\"\n\n"
			"Ограничения:\n"
			f"- Допустимые категории тегов: {', '.join(tax_cat_list)}\n"
			"- Для каждой категории разрешены только значения, реально встречающиеся в БД.\n"
			"- Если подходящего значения нет, не добавляй его в tags.\n"
			"- Ключевые слова (keywords) — свободная форма на русском, до 6 штук.\n"
			"- Альтернативные запросы (alt_queries) — 2–4 перефраза для полнотекстового поиска.\n\n"
			"Формат ответа (строгий JSON):\n"
			"{\n"
			"  \"tags\": {\"<category>\": [\"<value>\", \"<value2>\"]},\n"
			"  \"keywords\": [\"...\"],\n"
			"  \"alt_queries\": [\"...\", \"...\"]\n"
			"}\n"
		)
		txt = await llm.chat(system=system, user=user, temperature=0.0)
		try:
			data = json.loads((txt or "").strip())
		except Exception:
			return {"tags": {}, "keywords": [], "alt_queries": []}
		# Валидация по известной таксономии
		tags: Dict[str, List[str]] = {}
		raw_tags = data.get("tags") or {}
		for cat, vals in raw_tags.items():
			if cat in known and isinstance(vals, list):
				filtered_vals = [v for v in vals if isinstance(v, str) and v in known[cat]]
				if filtered_vals:
					tags[cat] = filtered_vals
		keywords = [k for k in (data.get("keywords") or []) if isinstance(k, str)]
		alt_queries = [q for q in (data.get("alt_queries") or []) if isinstance(q, str)]
		return {"tags": tags, "keywords": keywords, "alt_queries": alt_queries}

	async def _rerank_async(self, llm: AsyncLLMClient, query: str, personas: List[Persona], top_k: int = 10) -> List[Persona]:
		# Параллельное ранжирование через LLM, усечённый профиль для снижения латентности
		system = "Ты — ассистент по поиску релевантных персон. Отвечай только числом от 0.0 до 1.0."
		async def score(p: Persona) -> Tuple[float, Persona]:
			user = (
				"Пользователь описывает целевую персону так:\n"
				f"\"{query.strip()}\"\n\n"
				"Профиль персоны:\n"
				f"{p.title}\n\n"
				f"{(p.profile_md or '')[:800]}\n\n"
				"Верни ТОЛЬКО одно число от 0.0 до 1.0 — оценку релевантности. Без пояснений."
			)
			try:
				txt = await llm.chat(system=system, user=user, temperature=0.0, max_tokens=16)
				return float(str(txt).replace(",", ".").strip()), p
			except Exception:
				return 0.0, p
		results = await asyncio.gather(*[score(p) for p in personas], return_exceptions=False)
		results.sort(key=lambda x: x[0], reverse=True)
		return [p for _, p in results[:top_k]]

	async def search_by_description_fast(self, query: str, llm: AsyncLLMClient, k_fts: int = 40, top_k: int = 12) -> List[Persona]:
		key = f"nl2:{query.strip().lower()}:{k_fts}:{top_k}"
		cached = await self._cache.get(key)
		if isinstance(cached, list):
			return cached  # type: ignore[return-value]
		# 1) FTS
		candidates = await self.fts_candidates(query, k=k_fts)
		# 2) Фолбэк
		if not candidates:
			mapped = await self._llm_map_description_to_filters_async(llm, query)
			include_any: Dict[str, List[str]] = {}
			for cat, vals in (mapped.get("tags") or {}).items():
				include_any[str(cat)] = [str(v) for v in vals]
			tag_hits = await self.search_by_filters({}, include_any, {}, title_like=None, limit=400) if include_any else []
			alt_hits: List[Persona] = []
			for alt in (mapped.get("alt_queries") or [])[:4]:
				alt_hits.extend(await self.fts_candidates(alt, k=max(10, k_fts // 2)))
			combined: Dict[str, Persona] = {}
			for p in tag_hits + alt_hits:
				combined[p.persona_id] = p
			candidates = list(combined.values())
		if not candidates:
			return []
		# 3) Реренж
		ranked = await self._rerank_async(llm, query, candidates, top_k=top_k)
		await self._cache.set(key, ranked, ttl_s=1800.0)
		return ranked

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


