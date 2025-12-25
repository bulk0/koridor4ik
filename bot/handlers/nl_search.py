from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.async_llm import AsyncLLMClient
from ..services.persona_search import PersonaSearchService
from ..keyboards import candidates_selection_kb, refine_search_kb
from ..services.logger import log_event

router = Router()
_search = PersonaSearchService()

@router.message(DialogStates.nl_query)
async def nl_query(message: Message, state: FSMContext) -> None:
	query = (message.text or "").strip()
	if not query:
		await message.answer("Пустой запрос. Опишите, с кем хотите поговорить.")
		return
	llm = AsyncLLMClient()
	if message.from_user:
		log_event(message.from_user.id, "auto", "preflight_llm")
	info = await llm.preflight_check()
	if message.from_user:
		log_event(message.from_user.id, "auto", "preflight_llm_ok", model=info.get("model"), supports=info.get("supports"))
	personas = await _search.search_by_description(query, llm, k_fts=50, top_k=15)
	if not personas:
		await message.answer(
			"Ничего не нашли. Попробуйте упростить запрос или снизить ожидания (уберите редкие условия, не уточняйте город/возраст/дети/поисковик).",
			reply_markup=refine_search_kb(),
		)
		if message.from_user:
			log_event(message.from_user.id, "auto", "nl_search_empty", query=query)
		return
	# Если много совпадений — показываем 5 примеров и просим уточнить
	if len(personas) >= 10:
		lines = ["Совпадений много. Примеры (5):"]
		for idx, p in enumerate(personas[:5], 1):
			lines.append(f"{idx}) {p.title}")
		lines.append("\nМожете уточнить (город, возраст, дети, поисковик), либо нажмите «Попробовать ещё раз».")
		await message.answer("\n".join(lines), reply_markup=refine_search_kb())
		await state.set_state(DialogStates.nl_query)
		return
	# Показать кандидатов для выбора
	await state.update_data(nl_personas=[(p.persona_id, p.title) for p in personas], cand_page=0, cand_selected=[])
	await state.set_state(DialogStates.nl_candidates)
	await _show_candidates_page(message, personas, page=0, selected=set())
	if message.from_user:
		log_event(message.from_user.id, "auto", "nl_search_ok", query=query, n_candidates=len(personas))

async def _show_candidates_page(message: Message, personas, page: int, selected: set[int]) -> None:
	kb = candidates_selection_kb([(p.persona_id, p.title) for p in personas], selected=selected, page=page, page_size=5)
	await message.answer(
		"Выберите собеседников (нажимайте на пункты, затем «Готово»). Можно также написать краткое описание в ответ.",
		reply_markup=kb,
	)


