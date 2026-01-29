from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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
	def _infer_tags_from_query(self, query: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, List[str]]]:
		"""
		Извлекает теги из запроса на естественном языке.
		Возвращает (include_all, include_any, exclude) — жёсткие, мягкие фильтры и исключения.
		
		Примеры:
		- 'айтишников из москвы' -> include_all={profession: [it], city_name: [москва]}
		- 'декретницы не из москвы' -> include_all={profession: [homemaker]}, exclude={city_name: [москва]}
		- 'не студенты' -> exclude={profession: [student]}
		- 'без chatgpt' -> exclude={ai_services: [chatgpt]}
		"""
		import re
		q = query.lower()
		include_all: Dict[str, List[str]] = {}
		include_any: Dict[str, List[str]] = {}
		exclude: Dict[str, List[str]] = {}
		
		# === Обработка исключений: "не из москвы", "без москвы", "кроме москвы", "не студенты" ===
		exclude_patterns = [
			r"не из\s+(\S+)",
			r"без\s+(\S+)",
			r"кроме\s+(\S+)",
			r"не\s+(\S+)",
		]
		excluded_tokens: set[str] = set()
		for pattern in exclude_patterns:
			for match in re.finditer(pattern, q):
				token = match.group(1).strip(",.;:")
				excluded_tokens.add(token)
		
		def is_keyword_excluded(keywords: List[str]) -> bool:
			"""Проверяет, упоминается ли ключевое слово в контексте исключения."""
			for kw in keywords:
				# Прямое совпадение с токеном исключения
				if kw in excluded_tokens:
					return True
				# Частичное совпадение (москв -> москвы)
				for et in excluded_tokens:
					if kw in et or et in kw:
						return True
			return False
		
		def add_tag(category: str, tag: str, keywords: List[str], is_strict: bool = True) -> None:
			"""Добавляет тег в include или exclude в зависимости от контекста."""
			if is_keyword_excluded(keywords):
				exclude.setdefault(category, []).append(tag)
			elif is_strict:
				include_all.setdefault(category, []).append(tag)
			else:
				include_any.setdefault(category, []).append(tag)
		
		# === Профессии ===
		profession_map = {
			"it": ["айтишник", "программист", "разработчик", "it-", "айти", "девелопер", "девопс", "devops", "сисадмин", "backend", "frontend", "it специалист"],
			"engineer": ["инженер", "конструктор", "технолог"],
			"manager": ["руководител", "директор", "менеджер", "управленец", "начальник"],
			"sales": ["продажник", "продавец", "менеджер по продаж"],
			"marketing": ["маркетолог", "smm", "рекламщик", "пиарщик"],
			"finance": ["бухгалтер", "экономист", "финансист"],
			"legal": ["юрист", "адвокат", "нотариус"],
			"medical": ["врач", "медик", "фельдшер", "медсестр", "доктор"],
			"education": ["учитель", "преподаватель", "репетитор", "педагог"],
			"student": ["студент", "аспирант", "учащийся"],
			"homemaker": ["домохозяйк", "в декрете", "мама в декрете", "декретниц"],
			"entrepreneur": ["предприниматель", "бизнесмен", "бизнесвумен", "владелец бизнеса"],
			"creative": ["дизайнер", "художник", "фотограф", "архитектор"],
			"media": ["журналист", "блогер", "видеоблогер"],
			"hr": ["hr", "кадровик", "рекрутер", "эйчар"],
		}
		for prof_tag, keywords in profession_map.items():
			if any(kw in q for kw in keywords):
				add_tag("profession", prof_tag, keywords, is_strict=True)
				break
		
		# === Города ===
		city_map = {
			"москва": ["москв", "мск"],
			"санкт-петербург": ["петербург", "питер", "спб"],
			"екатеринбург": ["екатеринбург", "екб"],
			"новосибирск": ["новосибирск"],
			"казань": ["казан"],
			"нижний новгород": ["нижний новгород", "нижн"],
			"челябинск": ["челябинск"],
			"самара": ["самар"],
			"ростов-на-дону": ["ростов"],
			"краснодар": ["краснодар"],
			"воронеж": ["воронеж"],
			"сочи": ["сочи"],
			"ярославль": ["ярославл"],
		}
		for city_tag, keywords in city_map.items():
			if any(kw in q for kw in keywords):
				add_tag("city_name", city_tag, keywords, is_strict=True)
				break
		
		# === Пол ===
		gender_map = {
			"female": ["девушк", "женщин", "женск", "девчонк"],
			"male": ["парн", "юнош", "мужчин", "мальчик", "мужик"],
		}
		for gender_tag, keywords in gender_map.items():
			if any(kw in q for kw in keywords):
				add_tag("gender", gender_tag, keywords, is_strict=True)
				break
		
		# === Возраст ===
		if any(w in q for w in ["молод", "юн"]):
			include_any.setdefault("age", []).extend(["18-24", "15-34"])
		if any(w in q for w in ["студент"]) and "profession" not in include_all and "profession" not in exclude:
			include_any.setdefault("age", []).extend(["18-24", "15-34"])
		if any(w in q for w in ["взросл", "средн"]):
			include_any.setdefault("age", []).extend(["25-34", "35-44"])
		if any(w in q for w in ["старш", "пожил"]):
			include_any.setdefault("age", []).extend(["45-55"])
		
		# === AI сервисы ===
		ai_map = {
			"chatgpt": ["chatgpt", "чатгпт", "чат гпт", "gpt", "гпт", "openai"],
			"aliceai": ["алис", "яндекс"],
			"deepseek": ["дипсик", "deepseek"],
			"gigachat": ["гигачат", "gigachat", "сбер"],
			"claude": ["клод", "claude", "антропик"],
			"gemini": ["джемини", "gemini", "гугл ai"],
		}
		for ai_tag, keywords in ai_map.items():
			if any(kw in q for kw in keywords):
				add_tag("ai_services", ai_tag, keywords, is_strict=False)
		
		# === Дети ===
		if any(w in q for w in ["с детьми", "с ребёнком", "с ребенком", "родител"]):
			include_all["children"] = ["True"]
		if any(w in q for w in ["без детей", "бездетн"]):
			include_all["children"] = ["False"]
		
		# Очистка дублей
		for d in [include_all, include_any, exclude]:
			for k, v in list(d.items()):
				d[k] = list(dict.fromkeys(v))
		
		return include_all, include_any, exclude

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
		"""
		Новая логика поиска:
		1. Сначала парсим запрос и ищем по тегам (profession, city_name, gender и т.д.)
		2. Если по тегам >= 3 результатов — возвращаем их (с LLM-реранжированием)
		3. Если по тегам < 3 результатов — добавляем FTS + LLM маппинг
		4. Результаты фильтруются по exclude-тегам
		"""
		key = f"nl4:{query.strip().lower()}:{k_fts}:{top_k}"
		cached = await self._cache.get(key)
		if isinstance(cached, list):
			return cached  # type: ignore[return-value]
		
		# 1) Парсим запрос в теги (включая exclude)
		include_all, include_any, exclude = self._infer_tags_from_query(query)
		
		candidates: List[Persona] = []
		has_strong_filters = bool(include_all) or bool(exclude)  # Есть ли жёсткие критерии
		
		# 2) Если есть фильтры — ищем по тегам
		if include_all or include_any or exclude:
			candidates = await self.search_by_filters(include_all, include_any, exclude, title_like=None, limit=100)
		
		# 3) Если по тегам мало результатов (< 3) И нет жёстких фильтров — добавляем FTS
		# Если есть жёсткие фильтры (profession, city, exclude) — не добавляем мусор из FTS
		if len(candidates) < 3 and not has_strong_filters:
			fts_hits = await self.fts_candidates(query, k=k_fts)
			# Применяем exclude-фильтры к FTS результатам
			if exclude:
				fts_hits = await self._apply_exclude_filter(fts_hits, exclude)
			# Объединяем, избегая дублей
			existing_ids = {p.persona_id for p in candidates}
			for p in fts_hits:
				if p.persona_id not in existing_ids:
					candidates.append(p)
					existing_ids.add(p.persona_id)
		
		# 4) Если по тегам мало результатов — пробуем ослабить фильтры
		# Но только если есть жёсткие фильтры и они дали мало результатов
		if len(candidates) < 3 and has_strong_filters:
			# Пробуем убрать один из жёстких фильтров (приоритет: город -> профессия)
			relaxed_candidates: List[Persona] = []
			
			# Сначала пробуем только профессию (без города)
			if "profession" in include_all:
				prof_only = {"profession": include_all["profession"]}
				relaxed_candidates = await self.search_by_filters(prof_only, include_any, exclude, title_like=None, limit=50)
			
			# Если всё ещё мало — пробуем только город
			if len(relaxed_candidates) < 3 and "city_name" in include_all:
				city_only = {"city_name": include_all["city_name"]}
				city_hits = await self.search_by_filters(city_only, include_any, exclude, title_like=None, limit=50)
				existing_ids = {p.persona_id for p in relaxed_candidates}
				for p in city_hits:
					if p.persona_id not in existing_ids:
						relaxed_candidates.append(p)
						existing_ids.add(p.persona_id)
			
			# Добавляем к кандидатам, помечая что это "ослабленный" поиск
			existing_ids = {p.persona_id for p in candidates}
			for p in relaxed_candidates:
				if p.persona_id not in existing_ids:
					candidates.append(p)
					existing_ids.add(p.persona_id)
		
		# 5) Если совсем ничего нет — пробуем LLM маппинг (только без жёстких фильтров)
		if len(candidates) < 3 and not has_strong_filters:
			mapped = await self._llm_map_description_to_filters_async(llm, query)
			llm_include_any: Dict[str, List[str]] = {}
			for cat, vals in (mapped.get("tags") or {}).items():
				llm_include_any[str(cat)] = [str(v) for v in vals]
			
			if llm_include_any:
				llm_hits = await self.search_by_filters({}, llm_include_any, exclude, title_like=None, limit=50)
				existing_ids = {p.persona_id for p in candidates}
				for p in llm_hits:
					if p.persona_id not in existing_ids:
						candidates.append(p)
						existing_ids.add(p.persona_id)
			
			# FTS по альтернативным запросам
			for alt in (mapped.get("alt_queries") or [])[:3]:
				alt_hits = await self.fts_candidates(alt, k=20)
				if exclude:
					alt_hits = await self._apply_exclude_filter(alt_hits, exclude)
				existing_ids = {p.persona_id for p in candidates}
				for p in alt_hits:
					if p.persona_id not in existing_ids:
						candidates.append(p)
						existing_ids.add(p.persona_id)
		
		if not candidates:
			return []
		
		# 6) Реранжирование через LLM (ограничиваем top_k, чтобы не возвращать лишнее)
		# Если по тегам нашли достаточно — ограничиваем количеством найденного
		effective_top_k = min(top_k, len(candidates)) if has_strong_filters else top_k
		ranked = await self._rerank_async(llm, query, candidates, top_k=effective_top_k)
		await self._cache.set(key, ranked, ttl_s=1800.0)
		return ranked
	
	async def _apply_exclude_filter(self, personas: List[Persona], exclude: Dict[str, List[str]]) -> List[Persona]:
		"""Фильтрует персон по exclude-тегам."""
		if not exclude:
			return personas
		from chat.talk import tags_for_persona
		filtered: List[Persona] = []
		for p in personas:
			tag_map = tags_for_persona(p.persona_id)
			ok = True
			for cat, vals in exclude.items():
				pvals = set(tag_map.get(cat, []))
				if pvals.intersection(set(vals)):
					ok = False
					break
			if ok:
				filtered.append(p)
		return filtered

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


