from __future__ import annotations

import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.persona_search import PersonaSearchService
from ..keyboards import refine_search_kb
from ..services.logger import log_event

router = Router()
_search = PersonaSearchService()

def _format_catalog(catalog: dict) -> str:
	lines = ["Список фильтров (ключ — примеры значений):"]
	for cat, pairs in catalog.items():
		values = ", ".join([v for v, _ in pairs[:6]])
		lines.append(f"- {cat}: {values}{' …' if len(pairs) > 6 else ''}")
	return "\n".join(lines)

def _parse_filter_dsl(text: str):
	"""Старый DSL-парсер: и: key=val; или: key=val"""
	include_all = {}
	include_any = {}
	exclude = {}
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	for line in lines:
		mode, rhs = ("и", line)
		if ":" in line:
			mode, rhs = line.split(":", 1)
		mode = mode.strip().lower()
		assignments = [p.strip() for p in rhs.split(";") if p.strip()]
		for a in assignments:
			if "=" not in a:
				continue
			key, vals = a.split("=", 1)
			values = [v.strip() for v in vals.split(",") if v.strip()]
			if not values:
				continue
			if mode == "и":
				include_all[key.strip()] = values
			elif mode == "или":
				include_any[key.strip()] = values
			elif mode == "не":
				exclude[key.strip()] = values
	return include_all, include_any, exclude

async def _parse_natural_language(text: str, catalog: dict) -> tuple[dict, dict, dict]:
	"""
	Умный парсер естественного языка.
	Поддерживает: 'Москва, 35-44, без детей', 'женщины, chatgpt или aliceai'
	"""
	include_all: dict[str, list[str]] = {}
	include_any: dict[str, list[str]] = {}
	exclude: dict[str, list[str]] = {}
	
	# Собираем все известные значения из каталога (value -> category)
	value_to_cat: dict[str, str] = {}
	for cat, pairs in catalog.items():
		for val, _ in pairs:
			value_to_cat[val.lower()] = cat
	
	# Нормализуем текст
	text_lower = text.lower().strip()
	
	# Обрабатываем исключения: "без детей", "не москва", "без chatgpt"
	exclude_patterns = [
		r"без\s+(\S+)",
		r"не\s+(\S+)",
		r"кроме\s+(\S+)",
	]
	for pattern in exclude_patterns:
		for match in re.finditer(pattern, text_lower):
			token = match.group(1).strip(",.;")
			# Пробуем найти в каталоге
			if token in value_to_cat:
				cat = value_to_cat[token]
				exclude.setdefault(cat, []).append(token)
			# Специальные случаи
			elif token in ("детей", "детьми", "ребёнка", "ребенка"):
				exclude.setdefault("children", []).append("True")
	
	# Обрабатываем "или" конструкции: "chatgpt или aliceai"
	or_pattern = r"(\S+)\s+или\s+(\S+)"
	for match in re.finditer(or_pattern, text_lower):
		val1, val2 = match.group(1).strip(",.;"), match.group(2).strip(",.;")
		for val in [val1, val2]:
			if val in value_to_cat:
				cat = value_to_cat[val]
				include_any.setdefault(cat, []).append(val)
	
	# Разбиваем на токены (убираем уже обработанные конструкции)
	cleaned = text_lower
	for pattern in exclude_patterns + [or_pattern]:
		cleaned = re.sub(pattern, " ", cleaned)
	
	tokens = [t.strip(",.;:") for t in re.split(r"[\s,;]+", cleaned) if t.strip(",.;:")]
	
	for token in tokens:
		# Пропускаем служебные слова
		if token in ("и", "с", "в", "на", "из", "для", "по", "а", "но", "же", "ли"):
			continue
		
		# Прямое совпадение в каталоге
		if token in value_to_cat:
			cat = value_to_cat[token]
			# Если уже добавлен в include_any (через "или"), пропускаем
			if cat in include_any and token in include_any[cat]:
				continue
			include_all.setdefault(cat, []).append(token)
			continue
		
		# Синонимы для gender
		if token in ("женщины", "женщина", "женский", "женск", "девушки", "девушка"):
			include_all.setdefault("gender", []).append("female")
		elif token in ("мужчины", "мужчина", "мужской", "мужск", "парни", "парень"):
			include_all.setdefault("gender", []).append("male")
		
		# Синонимы для children
		elif token in ("дети", "ребёнок", "ребенок", "есть_дети"):
			include_all.setdefault("children", []).append("True")
		
		# Возрастные группы — прямой матч (например "35-44")
		elif re.match(r"^\d+-\d+$", token):
			include_all.setdefault("age", []).append(token)
		
		# Частичный матч для городов и сервисов (начало слова)
		else:
			for val, cat in value_to_cat.items():
				if val.startswith(token) and len(token) >= 3:
					include_all.setdefault(cat, []).append(val)
					break
	
	return include_all, include_any, exclude

@router.message(DialogStates.filter_intro)
async def filter_intro(message: Message, state: FSMContext) -> None:
	# Этот хэндлер больше не нужен — сразу переходим в filter_collect из start.py
	# Но оставляем на случай, если кто-то попадёт в это состояние
	catalog = await _search.tags_catalog()
	text = _format_catalog(catalog)
	text += (
		"\n\nОпишите, кого ищете — естественным языком или через фильтры.\n"
		"Примеры:\n"
		"• Москва, 35-44, без детей\n"
		"• женщины, chatgpt или aliceai\n"
		"• city=Екатеринбург; age=25-34"
	)
	await message.answer(text, reply_markup=refine_search_kb())
	await state.set_state(DialogStates.filter_collect)

@router.message(DialogStates.filter_collect)
async def filter_collect(message: Message, state: FSMContext) -> None:
	text = message.text or ""
	catalog = await _search.tags_catalog()
	
	# Сначала пробуем старый DSL (если есть "=" — это точно DSL)
	if "=" in text:
		include_all, include_any, exclude = _parse_filter_dsl(text)
	else:
		# Парсим естественный язык
		include_all, include_any, exclude = await _parse_natural_language(text, catalog)
	
	# Логируем распознанные фильтры
	if message.from_user:
		log_event(
			message.from_user.id, "auto", "filter_parsed",
			raw_text=text,
			include_all=include_all,
			include_any=include_any,
			exclude=exclude,
		)
	
	personas = await _search.search_by_filters(include_all, include_any, exclude, title_like=None, limit=500)
	n = len(personas)
	
	if n == 0:
		# Показываем, что распознали, чтобы пользователь понял
		parsed_info = []
		if include_all:
			parsed_info.append(f"И: {include_all}")
		if include_any:
			parsed_info.append(f"ИЛИ: {include_any}")
		if exclude:
			parsed_info.append(f"НЕ: {exclude}")
		parsed_str = "\n".join(parsed_info) if parsed_info else "(ничего не распознано)"
		
		await message.answer(
			f"Ничего не нашли.\n\nРаспознанные фильтры:\n{parsed_str}\n\n"
			"Попробуйте упростить условия или используйте другие значения из списка.",
			reply_markup=refine_search_kb(),
		)
		if message.from_user:
			log_event(message.from_user.id, "auto", "filter_search_empty", include_all=include_all, include_any=include_any, exclude=exclude)
		return
	
	lines = [f"Найдено подходящих персон: {n}. Примеры:"]
	for i, p in enumerate(personas[:5], 1):
		lines.append(f"{i}) {p.title}")
	lines.append("\nНапишите номера (например: 1,3-5) или уточните запрос.")
	await state.update_data(fl_personas=[(p.persona_id, p.title) for p in personas])
	await state.set_state(DialogStates.filter_candidates)
	await message.answer("\n".join(lines), reply_markup=refine_search_kb())
	if message.from_user:
		log_event(message.from_user.id, "auto", "filter_search_ok", n_candidates=n)

@router.callback_query(F.data == "refine:popular")
async def on_popular_tags(callback: CallbackQuery, state: FSMContext) -> None:
	catalog = await _search.tags_catalog()
	text = _format_catalog(catalog)
	await callback.message.answer(text)
	await callback.answer()

@router.callback_query(F.data == "refine:retry")
async def on_retry(callback: CallbackQuery, state: FSMContext) -> None:
	await state.set_state(DialogStates.filter_collect)
	await callback.message.answer(
		"Опишите, кого ищете — естественным языком.\n"
		"Примеры:\n"
		"• Москва, 35-44, без детей\n"
		"• женщины, chatgpt или aliceai",
		reply_markup=refine_search_kb(),
	)
	await callback.answer()


