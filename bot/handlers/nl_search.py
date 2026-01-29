from __future__ import annotations

import random
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatAction
import asyncio
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.async_llm import AsyncLLMClient
from ..services.persona_search import PersonaSearchService
from ..keyboards import candidates_selection_kb, format_candidates_text, refine_search_kb, chat_controls_prompt_kb
from ..services.logger import log_event
from ..utils.safe_telegram import safe_answer, safe_edit, safe_typing

router = Router()
_search = PersonaSearchService()

def _format_catalog_brief(catalog: dict) -> str:
	lines = ["Популярные теги (ключ — примеры значений):"]
	for cat, pairs in catalog.items():
		values = ", ".join([v for v, _ in pairs[:6]])
		lines.append(f"- {cat}: {values}{' …' if len(pairs) > 6 else ''}")
	return "\n".join(lines)

@router.message(DialogStates.nl_query)
async def nl_query(message: Message, state: FSMContext) -> None:
	query = (message.text or "").strip()
	if not query:
		await message.answer("Пустой запрос. Опишите, с кем хотите поговорить.")
		return
	# Если ранее показывали примеры (nl_preview) — позволим выбрать по номеру или словом
	data_prev = await state.get_data()
	preview = data_prev.get("nl_preview") or []
	if preview:
		text_l = query.lower()
		chosen: list[tuple[str, str]] = []
		# Парсим список номеров: "1, 2, 3" или "1,2,3" или "1-3" или "1, 3-5"
		parts = [p.strip() for p in query.replace(" ", "").split(",") if p.strip()]
		for token in parts:
			if "-" in token:
				# диапазон: 1-3
				try:
					a, b = token.split("-", 1)
					for i in range(int(a), int(b) + 1):
						if 1 <= i <= len(preview):
							chosen.append(preview[i - 1])
				except ValueError:
					pass
			elif token.isdigit():
				i = int(token)
				if 1 <= i <= len(preview):
					chosen.append(preview[i - 1])
		# русские порядковые (только если ещё не выбрали)
		if not chosen:
			ord_map = {"перв": 1, "втор": 2, "треть": 3, "четв": 4, "пят": 5}
			for key, i in ord_map.items():
				if text_l.startswith(key) and 1 <= i <= len(preview):
					chosen = [preview[i - 1]]
					break
		# по фразе — простое включение (только если ещё не выбрали и есть буквы)
		if not chosen and any(ch.isalpha() for ch in text_l):
			for pid, title in preview:
				if all(tok in title.lower() for tok in text_l.split() if len(tok) >= 3):
					chosen = [(pid, title)]
					break
		if chosen:
			# убираем дубликаты, сохраняя порядок
			seen = set()
			unique_chosen = []
			for item in chosen:
				if item[0] not in seen:
					seen.add(item[0])
					unique_chosen.append(item)
			await state.update_data(chosen=unique_chosen)
			await state.set_state(DialogStates.chat)
			await message.answer(
				"Введите вопрос. Примеры:\n"
				"- Каким ИИ‑сервисом вы чаще пользуетесь и почему?\n"
				"- Как вы ищете информацию: через ИИ или поисковик?\n",
				reply_markup=chat_controls_prompt_kb(),
			)
			# очищаем превью, чтобы следующие сообщения шли в обычный поток
			await state.update_data(nl_preview=[])
			return
	llm = AsyncLLMClient()
	if message.from_user:
		log_event(message.from_user.id, "auto", "preflight_llm")
	info = await llm.preflight_check()
	if message.from_user:
		log_event(message.from_user.id, "auto", "preflight_llm_ok", model=info.get("model"), supports=info.get("supports"))
	# Прогресс: отбивки и typing
	status = await safe_answer(message, "Связываюсь с базой данных…")
	stop = asyncio.Event()
	async def typing_loop():
		while not stop.is_set():
			await safe_typing(message)
			await asyncio.sleep(4)
	typing_task = asyncio.create_task(typing_loop())
	search_note: str | None = None
	try:
		if status:
			await safe_edit(status, "Ищу кандидатов…")
		# Новый быстрый поиск с параллельным LLM‑реранжом
		# Возвращает SearchResult с пояснением, если фильтры были ослаблены
		search_result = await _search.search_by_description_fast(query, llm, k_fts=40, top_k=15)
		personas = list(search_result.personas)
		search_note = search_result.note
		if not personas:
			if status:
				await safe_edit(status, "Пробую умный поиск по смыслу…")
			personas = await _search.search_by_description(query, llm, k_fts=50, top_k=10)
	finally:
		stop.set()
		await typing_task
	if not personas:
		await message.answer(
			"Ничего не нашли. Попробуйте упростить запрос или снизить ожидания (уберите редкие условия, не уточняйте город/возраст/дети/поисковик).",
			reply_markup=refine_search_kb(),
		)
		if message.from_user:
			log_event(message.from_user.id, "auto", "nl_search_empty", query=query)
		return
	# Показываем пояснение, если фильтры были ослаблены
	if search_note:
		await message.answer(search_note)
	# Показываем окно с чекбоксами для любого количества персон > 5
	# (для <= 5 тоже показываем, чтобы был единый UX)
	personas_data = [(p.persona_id, p.title) for p in personas]
	await state.update_data(nl_personas=personas_data, cand_page=0, cand_selected=[])
	await state.set_state(DialogStates.nl_candidates)
	if status:
		await safe_edit(status, f"Найдено персон: {len(personas)}. Показываю список…")
	await _show_candidates_page(message, personas_data, page=0, selected=set())
	if message.from_user:
		log_event(message.from_user.id, "auto", "nl_search_ok", query=query, n_candidates=len(personas), note=search_note)

async def _show_candidates_page(message: Message, personas: list[tuple[str, str]], page: int, selected: set[int]) -> None:
	"""Показывает страницу кандидатов с полными названиями в тексте."""
	text = format_candidates_text(personas, selected=selected, page=page, page_size=5)
	kb = candidates_selection_kb(personas, selected=selected, page=page, page_size=5)
	await message.answer(text, reply_markup=kb)

# Коллбеки refine:* обрабатываются в handlers/refine.py


