from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.persona_search import PersonaSearchService
from ..keyboards import refine_search_kb
from ..utils.safe_telegram import safe_answer

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
	if cur and "filter_" in cur:
		text += (
			"\n\nНапишите условия отбора через И/или/не (одна или несколько строк):\n"
			"Примеры:\n"
			"и: city=Москва; age=35-44\n"
			"или: ai_services=chatgpt,aliceai\n"
			"не: children=True\n"
		)
	else:
		# nl_query: показали превью с номерами, теги — для ориентира
		data = await state.get_data()
		preview = data.get("nl_preview") or []
		if preview:
			text += (
				"\n\nТеги — для ориентира. Вы можете:\n"
				"- Написать номер персоны из списка выше (1–5)\n"
				"- Написать несколько номеров через запятую (1, 3, 5)\n"
				"- Уточнить запрос (например: «из Москвы» или «без детей»)"
			)
		else:
			text += "\n\nОпишите, с кем хотите поговорить (например: «мама в декрете из Москвы»)."
	await safe_answer(callback.message, text, reply_markup=refine_search_kb())
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


