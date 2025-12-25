from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..keyboards import mode_choice_kb
from ..services.logger import log_event

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
	await state.set_state(DialogStates.mode_choice)
	await message.answer(
		"Привет! Хочешь поговорить с кем-то конкретным или выбрать собеседников по фильтрам?",
		reply_markup=mode_choice_kb(),
	)
	if message.from_user:
		log_event(message.from_user.id, "auto", "start", text="/start")

@router.callback_query(F.data.startswith("mode:"))
async def on_mode_choice(callback: CallbackQuery, state: FSMContext) -> None:
	mode = callback.data.split(":", 1)[1]
	if mode == "nl":
		await state.set_state(DialogStates.nl_query)
		await callback.message.edit_text(
			"Напишите, с кем хотите пообщаться.\nПримеры: «пользователь нейросетей в декрете», «Екатерина из Екатеринбурга».",
		)
	elif mode == "filters":
		await state.set_state(DialogStates.filter_intro)
		await callback.message.edit_text(
			"Сейчас покажу список фильтров (ключ — значения). Выберите нужные и сформулируйте условия отбора через И/или/не.\n"
			"Примеры:\n"
			"- и: city=Москва; age=35-44\n"
			"- или: ai_services=chatgpt,aliceai\n"
			"- не: children=True\n"
		)
	if callback.from_user:
		log_event(callback.from_user.id, "auto", "mode_choice", mode=mode)
	await callback.answer()


