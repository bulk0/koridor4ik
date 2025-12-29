from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.persona_search import PersonaSearchService
from ..keyboards import refine_search_kb

router = Router()
_search = PersonaSearchService()

def _format_catalog(catalog: dict) -> str:
	lines = ["Популярные теги (ключ — примеры значений):"]
	for cat, pairs in catalog.items():
		values = ", ".join([v for v, _ in pairs[:6]])
		lines.append(f"- {cat}: {values}{' …' if len(pairs) > 6 else ''}")
	return "\n".join(lines)

@router.callback_query(F.data == "refine:popular")
async def show_popular_tags(callback: CallbackQuery, state: FSMContext) -> None:
	catalog = await _search.tags_catalog()
	text = _format_catalog(catalog)
	cur = await state.get_state()
	if cur and cur.startswith("DialogStates.filter_"):
		text += (
			"\n\nНапишите условия отбора через И/или/не (одна или несколько строк):\n"
			"Примеры:\n"
			"и: city=Москва; age=35-44\n"
			"или: ai_services=chatgpt,aliceai\n"
			"не: children=True\n"
		)
	else:
		text += "\n\nОпишите, с кем хотите поговорить (одной фразой)."
	await callback.message.answer(text, reply_markup=refine_search_kb())
	await callback.answer()

@router.callback_query(F.data == "refine:retry")
async def refine_retry(callback: CallbackQuery, state: FSMContext) -> None:
	cur = await state.get_state()
	if cur and cur.startswith("DialogStates.filter_"):
		# остаёмся в фильтровом сценарии, просто просим ввести условия заново
		await callback.message.answer(
			"Ок, давайте заново. Напишите условия отбора через И/или/не.\n"
			"Например:\n"
			"и: city=Москва; age=35-44\n"
			"или: ai_services=chatgpt,aliceai\n"
			"не: children=True",
			reply_markup=refine_search_kb(),
		)
		await state.set_state(DialogStates.filter_collect)
	else:
		await callback.message.answer("Ок, опишите, с кем хотите поговорить:", reply_markup=refine_search_kb())
		await state.set_state(DialogStates.nl_query)
	await callback.answer()


