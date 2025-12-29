from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def mode_choice_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Поговорить с конкретным человеком", callback_data="mode:nl")],
		[InlineKeyboardButton(text="Выбрать по фильтрам", callback_data="mode:filters")],
		[InlineKeyboardButton(text="Завершить диалог", callback_data="finish:dialog")],
	])

def welcome_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Начать", callback_data="start:go")],
	])

def candidates_selection_kb(personas: list[tuple[str, str]], selected: set[int], page: int, page_size: int = 5) -> InlineKeyboardMarkup:
	start = page * page_size
	end = start + page_size
	chunk = personas[start:end]
	rows = []
	for i, (_, title) in enumerate(chunk, start=start + 1):
		mark = "✅" if i in selected else "◻️"
		rows.append([InlineKeyboardButton(text=f"{mark} {i}) {title[:60]}", callback_data=f"pick:{i}")])
	nav = []
	if page > 0:
		nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"page:{page-1}"))
	if end < len(personas):
		nav.append(InlineKeyboardButton(text="Вперёд →", callback_data=f"page:{page+1}"))
	if nav:
		rows.append(nav)
	rows.append([
		InlineKeyboardButton(text="Готово", callback_data="cand:done"),
		InlineKeyboardButton(text="Очистить", callback_data="cand:clear"),
	])
	rows.append([InlineKeyboardButton(text="Завершить диалог", callback_data="finish:dialog")])
	return InlineKeyboardMarkup(inline_keyboard=rows)

def chat_controls_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Выгрузить ответы", callback_data="chat:export_answers")],
		[InlineKeyboardButton(text="Закончить", callback_data="chat:finish")],
	])

def chat_controls_prompt_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Закончить", callback_data="chat:finish")],
	])

def finish_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Выгрузить всю сессию", callback_data="chat:export_session")],
		[InlineKeyboardButton(text="Начать заново", callback_data="start:go")],
	])

def refine_search_kb() -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Попробовать ещё раз", callback_data="refine:retry")],
		[InlineKeyboardButton(text="Показать популярные теги", callback_data="refine:popular")],
		[InlineKeyboardButton(text="Завершить диалог", callback_data="finish:dialog")],
	])

def answer_kb(idx: int) -> InlineKeyboardMarkup:
	return InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="Сохранить этот ответ", callback_data=f"ans:save:{idx}")],
	])


