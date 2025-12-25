from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..keyboards import candidates_selection_kb
from ..services.logger import log_event

router = Router()

async def _refresh_candidates(message: Message, state: FSMContext) -> None:
	data = await state.get_data()
	personas = data.get("nl_personas") or data.get("fl_personas") or []
	page = int(data.get("cand_page", 0))
	selected = set(data.get("cand_selected") or [])
	kb = candidates_selection_kb(personas, selected=selected, page=page, page_size=5)
	await message.edit_reply_markup(reply_markup=kb)

@router.callback_query(F.data.startswith("page:"), DialogStates.nl_candidates)
@router.callback_query(F.data.startswith("page:"), DialogStates.filter_candidates)
async def on_page(callback: CallbackQuery, state: FSMContext) -> None:
	page = int(callback.data.split(":", 1)[1])
	await state.update_data(cand_page=page)
	await _refresh_candidates(callback.message, state)
	await callback.answer()

@router.callback_query(F.data.startswith("pick:"), DialogStates.nl_candidates)
@router.callback_query(F.data.startswith("pick:"), DialogStates.filter_candidates)
async def on_pick(callback: CallbackQuery, state: FSMContext) -> None:
	idx = int(callback.data.split(":", 1)[1])
	data = await state.get_data()
	selected = set(data.get("cand_selected") or [])
	if idx in selected:
		selected.remove(idx)
	else:
		selected.add(idx)
	await state.update_data(cand_selected=list(selected))
	await _refresh_candidates(callback.message, state)
	await callback.answer()

@router.callback_query(F.data == "cand:clear", DialogStates.nl_candidates)
@router.callback_query(F.data == "cand:clear", DialogStates.filter_candidates)
async def on_clear(callback: CallbackQuery, state: FSMContext) -> None:
	await state.update_data(cand_selected=[])
	await _refresh_candidates(callback.message, state)
	await callback.answer()

@router.callback_query(F.data == "cand:done", DialogStates.nl_candidates)
@router.callback_query(F.data == "cand:done", DialogStates.filter_candidates)
async def on_done(callback: CallbackQuery, state: FSMContext) -> None:
	data = await state.get_data()
	personas = data.get("nl_personas") or data.get("fl_personas") or []
	selected = sorted(set(data.get("cand_selected") or []))
	if not selected:
		await callback.answer("Ничего не выбрано")
		return
	chosen = []
	for i in selected:
		if 1 <= i <= len(personas):
			chosen.append(personas[i - 1])
	await state.update_data(chosen=chosen)
	await state.set_state(DialogStates.chat)
	await callback.message.edit_reply_markup(reply_markup=None)
	await callback.message.answer(
		"Введите вопрос. Примеры:\n"
		"- Каким ИИ‑сервисом вы чаще пользуетесь и почему?\n"
		"- Как вы ищете информацию: через ИИ или поисковик?\n",
	)
	if callback.from_user:
		log_event(callback.from_user.id, "auto", "candidates_done", n=len(chosen))
	await callback.answer()


