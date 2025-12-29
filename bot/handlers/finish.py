from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from ..keyboards import mode_choice_kb
from ..states import DialogStates

router = Router()

async def _reset_and_back_home(obj, state: FSMContext) -> None:
	await state.clear()
	if hasattr(obj, "answer"):
		await obj.answer("Спасибо, всего доброго.\nНачнём заново?", reply_markup=mode_choice_kb())
	else:
		await obj.message.answer("Спасибо, всего доброго.\nНачнём заново?", reply_markup=mode_choice_kb())

@router.callback_query(F.data.startswith("finish:"))
async def on_finish_callback(callback: CallbackQuery, state: FSMContext) -> None:
	await _reset_and_back_home(callback, state)
	await callback.answer()

@router.message(Command("finish"))
async def on_finish_cmd(message: Message, state: FSMContext) -> None:
	await _reset_and_back_home(message, state)

@router.message()
async def on_finish_text(message: Message, state: FSMContext) -> None:
	text = (message.text or "").strip().lower()
	if text in {"все", "всё", "закончить", "стоп", "выход"}:
		await _reset_and_back_home(message, state)
		return
	# иначе пропускаем — пусть обработают другие роутеры/состояния


