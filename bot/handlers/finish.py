from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

from ..keyboards import mode_choice_kb
from ..states import DialogStates

router = Router()

@router.callback_query(F.data.startswith("finish:"))
async def on_finish_callback(callback: CallbackQuery, state: FSMContext) -> None:
	await state.clear()
	await callback.message.answer(
		"Диалог завершён. Спасибо!\n\n"
		"Нажмите /start или выберите способ поиска, чтобы начать заново.",
		reply_markup=mode_choice_kb(),
	)
	await callback.answer()

async def _send_finish_message(message: Message, state: FSMContext) -> None:
	await state.clear()
	await message.answer(
		"Диалог завершён. Спасибо!\n\n"
		"Нажмите /start или выберите способ поиска, чтобы начать заново.",
		reply_markup=mode_choice_kb(),
	)

@router.message(Command("finish"))
async def on_finish_cmd(message: Message, state: FSMContext) -> None:
	await _send_finish_message(message, state)

@router.message()
async def on_finish_text(message: Message, state: FSMContext) -> None:
	text = (message.text or "").strip().lower()
	if text in {"все", "всё", "закончить", "стоп", "выход"}:
		await _send_finish_message(message, state)
		return
	# иначе пропускаем — пусть обработают другие роутеры/состояния


