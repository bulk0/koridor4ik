from __future__ import annotations

import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.persona_search import PersonaSearchService
from ..keyboards import refine_search_kb, candidates_selection_kb, format_candidates_text
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

# Словарь русских синонимов -> (category, value)
_RUSSIAN_SYNONYMS: dict[str, tuple[str, str]] = {
	# AI сервисы
	"чатгпт": ("ai_services", "chatgpt"),
	"чат-гпт": ("ai_services", "chatgpt"),
	"чатжпт": ("ai_services", "chatgpt"),
	"гпт": ("ai_services", "chatgpt"),
	"опенаи": ("ai_services", "chatgpt"),
	"openai": ("ai_services", "chatgpt"),
	"дипсик": ("ai_services", "deepseek"),
	"дипсика": ("ai_services", "deepseek"),
	"дипсиком": ("ai_services", "deepseek"),
	"дипсике": ("ai_services", "deepseek"),
	"алиса": ("ai_services", "aliceai"),
	"алисы": ("ai_services", "aliceai"),
	"алисой": ("ai_services", "aliceai"),
	"алису": ("ai_services", "aliceai"),
	"яндекс": ("ai_services", "aliceai"),
	"клод": ("ai_services", "claude"),
	"клода": ("ai_services", "claude"),
	"клодом": ("ai_services", "claude"),
	"антропик": ("ai_services", "claude"),
	"джемини": ("ai_services", "gemini"),
	"гемини": ("ai_services", "gemini"),
	"gemini": ("ai_services", "gemini"),
	"гугл": ("ai_services", "gemini"),
	"копайлот": ("ai_services", "copilot"),
	"копилот": ("ai_services", "copilot"),
	"copilot": ("ai_services", "copilot"),
	"майкрософт": ("ai_services", "copilot"),
	"перплексити": ("ai_services", "perplexity"),
	"перплексити": ("ai_services", "perplexity"),
	"мистраль": ("ai_services", "mistral"),
	"грок": ("ai_services", "grok"),
	"grok": ("ai_services", "grok"),
	# Поисковики
	"гугл": ("search_engine", "google"),
	"google": ("search_engine", "google"),
	"яндекс": ("search_engine", "yandex"),
	"yandex": ("search_engine", "yandex"),
	# Пол
	"женщины": ("gender", "female"),
	"женщина": ("gender", "female"),
	"женский": ("gender", "female"),
	"женского": ("gender", "female"),
	"женщин": ("gender", "female"),
	"девушки": ("gender", "female"),
	"девушка": ("gender", "female"),
	"девушек": ("gender", "female"),
	"мужчины": ("gender", "male"),
	"мужчина": ("gender", "male"),
	"мужской": ("gender", "male"),
	"мужского": ("gender", "male"),
	"мужчин": ("gender", "male"),
	"парни": ("gender", "male"),
	"парень": ("gender", "male"),
	"парней": ("gender", "male"),
	# Дети
	"дети": ("children", "True"),
	"детьми": ("children", "True"),
	"ребёнок": ("children", "True"),
	"ребенок": ("children", "True"),
	"ребёнком": ("children", "True"),
	"ребенком": ("children", "True"),
	# Города (популярные)
	"москва": ("city", "москва"),
	"москвы": ("city", "москва"),
	"москве": ("city", "москва"),
	"питер": ("city", "санкт-петербург"),
	"питера": ("city", "санкт-петербург"),
	"спб": ("city", "санкт-петербург"),
	"петербург": ("city", "санкт-петербург"),
	"петербурга": ("city", "санкт-петербург"),
	"екб": ("city", "екатеринбург"),
	"екатеринбург": ("city", "екатеринбург"),
	"екатеринбурга": ("city", "екатеринбург"),
	"новосибирск": ("city", "новосибирск"),
	"новосибирска": ("city", "новосибирск"),
	"казань": ("city", "казань"),
	"казани": ("city", "казань"),
}

def _normalize_token(token: str) -> tuple[str, str] | None:
	"""Пытается найти синоним для токена. Возвращает (category, value) или None."""
	# Убираем окончания для лучшего матчинга
	token_clean = token.rstrip("аеийоуыья")
	for synonym, (cat, val) in _RUSSIAN_SYNONYMS.items():
		syn_clean = synonym.rstrip("аеийоуыья")
		if token == synonym or token_clean == syn_clean or token.startswith(syn_clean) or syn_clean.startswith(token_clean):
			return (cat, val)
	return None

async def _parse_natural_language(text: str, catalog: dict) -> tuple[dict, dict, dict]:
	"""
	Умный парсер естественного языка.
	Поддерживает: 'Москва, 35-44, без детей', 'женщины, chatgpt или aliceai', 'пользователи дипсика'
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
	
	# Обрабатываем исключения: "без детей", "не москва", "без chatgpt", "без дипсика"
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
			# Специальные случаи для детей
			elif token in ("детей", "детьми", "ребёнка", "ребенка"):
				exclude.setdefault("children", []).append("True")
			else:
				# Проверяем синонимы
				synonym_match = _normalize_token(token)
				if synonym_match:
					cat, val = synonym_match
					exclude.setdefault(cat, []).append(val)
	
	# Обрабатываем "или" конструкции: "chatgpt или aliceai", "дипсик или алиса"
	or_pattern = r"(\S+)\s+или\s+(\S+)"
	for match in re.finditer(or_pattern, text_lower):
		val1, val2 = match.group(1).strip(",.;"), match.group(2).strip(",.;")
		for val in [val1, val2]:
			if val in value_to_cat:
				cat = value_to_cat[val]
				include_any.setdefault(cat, []).append(val)
			else:
				# Проверяем синонимы
				synonym_match = _normalize_token(val)
				if synonym_match:
					cat, normalized_val = synonym_match
					include_any.setdefault(cat, []).append(normalized_val)
	
	# Разбиваем на токены (убираем уже обработанные конструкции)
	cleaned = text_lower
	for pattern in exclude_patterns + [or_pattern]:
		cleaned = re.sub(pattern, " ", cleaned)
	
	tokens = [t.strip(",.;:") for t in re.split(r"[\s,;]+", cleaned) if t.strip(",.;:")]
	
	for token in tokens:
		# Пропускаем служебные слова
		if token in ("и", "с", "в", "на", "из", "для", "по", "а", "но", "же", "ли", "кто", "который", "которые", "пользователи", "пользователь", "люди", "человек"):
			continue
		
		# Прямое совпадение в каталоге
		if token in value_to_cat:
			cat = value_to_cat[token]
			# Если уже добавлен в include_any (через "или"), пропускаем
			if cat in include_any and token in include_any[cat]:
				continue
			include_all.setdefault(cat, []).append(token)
			continue
		
		# Проверяем русские синонимы
		synonym_match = _normalize_token(token)
		if synonym_match:
			cat, val = synonym_match
			if cat in include_any and val in include_any[cat]:
				continue
			include_all.setdefault(cat, []).append(val)
			continue
		
		# Возрастные группы — прямой матч (например "35-44")
		if re.match(r"^\d+-\d+$", token):
			include_all.setdefault("age", []).append(token)
			continue
		
		# Частичный матч для значений из каталога (начало слова, минимум 3 символа)
		if len(token) >= 3:
			for val, cat in value_to_cat.items():
				if val.startswith(token) or token.startswith(val[:3]):
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
	
	# Показываем окно с чекбоксами для выбора персон
	personas_data = [(p.persona_id, p.title) for p in personas]
	await state.update_data(fl_personas=personas_data, cand_page=0, cand_selected=[])
	await state.set_state(DialogStates.filter_candidates)
	
	text = format_candidates_text(personas_data, selected=set(), page=0, page_size=5)
	text = f"Найдено подходящих персон: {n}.\n\n{text}"
	kb = candidates_selection_kb(personas_data, selected=set(), page=0, page_size=5)
	await message.answer(text, reply_markup=kb)
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


