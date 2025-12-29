from __future__ import annotations

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.persona_search import PersonaSearchService
from ..keyboards import refine_search_kb
from ..services.logger import log_event

router = Router()
_search = PersonaSearchService()

def _format_catalog(catalog: dict) -> str:
	lines = ["Список фильтров (ключ — примеры значений):"]
	for cat, pairs in catalog.items():
		values = ", ".join([v for v, _ in pairs[:6]])
		lines.append(f"- {cat}: {values}{' …' if len(pairs) > 6 else ''}")
	return "\n".join(lines)

def _parse_filter_dsl(text: str):
	include_all = {}
	include_any = {}
	exclude = {}
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	for line in lines:
		mode, rhs = ("и", line)
		if ":" in line:
			mode, rhs = line.split(":", 1)
		mode = mode.strip().lower()
		assignments = [p.strip() for p in rhs.split(";") if p.strip()]
		for a in assignments:
			if "=" not in a:
				continue
			key, vals = a.split("=", 1)
			values = [v.strip() for v in vals.split(",") if v.strip()]
			if not values:
				continue
			if mode == "и":
				include_all[key.strip()] = values
			elif mode == "или":
				include_any[key.strip()] = values
			elif mode == "не":
				exclude[key.strip()] = values
	return include_all, include_any, exclude

@router.message(DialogStates.filter_intro)
async def filter_intro(message: Message, state: FSMContext) -> None:
	catalog = await _search.tags_catalog()
	text = _format_catalog(catalog)
	text += (
		"\n\nНапишите условия отбора через И/или/не (одна или несколько строк):\n"
		"Примеры:\n"
		"и: city=Москва; age=35-44\n"
		"или: ai_services=chatgpt,aliceai\n"
		"не: children=True\n"
	)
	await message.answer(text, reply_markup=refine_search_kb())
	await state.set_state(DialogStates.filter_collect)

@router.message(DialogStates.filter_collect)
async def filter_collect(message: Message, state: FSMContext) -> None:
	include_all, include_any, exclude = _parse_filter_dsl(message.text or "")
	personas = await _search.search_by_filters(include_all, include_any, exclude, title_like=None, limit=500)
	n = len(personas)
	if n == 0:
		await message.answer(
			"Ничего не нашли по заданным фильтрам. Попробуйте упростить условия (меньше И/НЕ).",
			reply_markup=refine_search_kb(),
		)
		if message.from_user:
			log_event(message.from_user.id, "auto", "filter_search_empty", include_all=include_all, include_any=include_any, exclude=exclude)
		return
	lines = [f"Найдено подходящих персон: {n}. Примеры:"]
	for i, p in enumerate(personas[:5], 1):
		lines.append(f"{i}) {p.title}")
	lines.append("Если подходит — напишите номера (например: 1,3-5) или краткое описание для выбора.")
	await state.update_data(fl_personas=[(p.persona_id, p.title) for p in personas])
	await state.set_state(DialogStates.filter_candidates)
	await message.answer("\n".join(lines), reply_markup=refine_search_kb())
	if message.from_user:
		log_event(message.from_user.id, "auto", "filter_search_ok", n_candidates=n)


