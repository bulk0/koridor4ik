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
	text = (
		"Привет! Я помогу поговорить с синтетическими персонами из исследовательской базы.\n\n"
		"Как это работает:\n"
		"1) Найдём собеседника — по описанию (естественным языком) или по фильтрам (город, возраст, интересы, сервисы и др.).\n"
		"2) Выберете одну или несколько персон.\n"
		"3) Зададите вопрос — ответы придут от лица выбранных персон. Можно сохранять отдельные ответы и весь диалог в файлы.\n\n"
		"Подсказки:\n"
		"- Если предложу список примеров, можно писать просто номер (например, «3» или «3)»).\n"
		"- Во время поиска и ответов покажу прогресс.\n\n"
		"Выберите способ поиска:"
	)
	await message.answer(text, reply_markup=mode_choice_kb())
	if message.from_user:
		log_event(message.from_user.id, "auto", "start", text="/start")

@router.callback_query(F.data == "start:go")
async def on_start_go(callback: CallbackQuery, state: FSMContext) -> None:
	await state.set_state(DialogStates.mode_choice)
	await callback.message.answer("Выберите способ поиска:", reply_markup=mode_choice_kb())
	await callback.answer()

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


