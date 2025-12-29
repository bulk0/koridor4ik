from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
import difflib

from ..states import DialogStates
from ..keyboards import candidates_selection_kb, chat_controls_prompt_kb, chat_controls_kb
from ..services.logger import log_event

router = Router()

async def _refresh_candidates(message: Message, state: FSMContext) -> None:
	data = await state.get_data()
	personas = data.get("nl_personas") or data.get("fl_personas") or []
	page = int(data.get("cand_page", 0))
	selected = set(data.get("cand_selected") or [])
	kb = candidates_selection_kb(personas, selected=selected, page=page, page_size=5)
	await message.edit_reply_markup(reply_markup=kb)

def _select_by_phrase_text(candidates: list[tuple[str, str]], phrase: str) -> list[tuple[str, str]]:
	phrase_l = phrase.lower()
	out: list[tuple[str, str]] = []
	# простое включение всех ключевых слов
	toks = [t for t in phrase_l.split() if len(t) >= 3]
	for pid, title in candidates:
		hay = title.lower()
		if all(t in hay for t in toks):
			out.append((pid, title))
	if out:
		return out[:5]
	# fuzzy matching как запасной вариант
	scores = []
	for pid, title in candidates:
		scores.append((difflib.SequenceMatcher(None, phrase_l, title.lower()).ratio(), (pid, title)))
	scores.sort(reverse=True, key=lambda x: x[0])
	return [itm for sc, itm in scores if sc >= 0.65][:3]

@router.message(DialogStates.nl_candidates)
@router.message(DialogStates.filter_candidates)
async def choose_text_candidates(message: Message, state: FSMContext) -> None:
	data = await state.get_data()
	personas = data.get("nl_personas") or data.get("fl_personas") or []
	if not personas:
		await message.answer("Список кандидатов пуст. Вернитесь к началу.")
		return
	text = (message.text or "").strip().lower()
	if not text:
		await message.answer("Напишите номер(а) или краткое описание персоны.")
		return
	# поддержка индексов, диапазонов и порядковых числительных
	by_idx = {str(i): personas[i - 1] for i in range(1, len(personas) + 1)}
	chosen: list[tuple[str, str]] = []
	parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
	ord_map = {
		"перва": 1, "перву": 1, "перв": 1,
		"втора": 2, "втору": 2, "втор": 2,
		"треть": 3, "третью": 3, "трет": 3,
		"четв": 4,
		"пят": 5,
		"шест": 6,
		"седьм": 7,
		"восьм": 8,
		"девят": 9,
		"десят": 10,
	}
	for token in parts:
		num_only = "".join(ch for ch in token if ch.isdigit())
		if num_only and num_only in by_idx:
			chosen.append(by_idx[num_only])
			continue
		sep = "-" if "-" in token else ("–" if "–" in token else None)
		if sep:
			try:
				a, b = token.split(sep, 1)
				ai, bi = int("".join(ch for ch in a if ch.isdigit())), int("".join(ch for ch in b if ch.isdigit()))
				for i in range(ai, bi + 1):
					if str(i) in by_idx:
						chosen.append(by_idx[str(i)])
				continue
			except Exception:
				pass
		for key, i in ord_map.items():
			if token.startswith(key) and str(i) in by_idx:
				chosen.append(by_idx[str(i)])
				break
	# если ничего не распознали — попробуем строкой
	if not chosen and any(ch.isalpha() for ch in text):
		chosen = _select_by_phrase_text(personas, text)
	if not chosen:
		await message.answer("Не понял выбор. Укажите индексы (например, 1,3-5) или краткое описание из заголовка.")
		return
	# снять дубликаты
	seen: set[str] = set()
	final: list[tuple[str, str]] = []
	for pid, title in chosen:
		if pid not in seen:
			final.append((pid, title))
			seen.add(pid)
	await state.update_data(chosen=final)
	await state.set_state(DialogStates.chat)
	await message.answer(
		"Введите вопрос. Примеры:\n"
		"- Каким ИИ‑сервисом вы чаще пользуетесь и почему?\n"
		"- Как вы ищете информацию: через ИИ или поисковик?\n",
		reply_markup=chat_controls_prompt_kb(),
	)

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
		reply_markup=chat_controls_prompt_kb(),
	)
	if callback.from_user:
		log_event(callback.from_user.id, "auto", "candidates_done", n=len(chosen))
	await callback.answer()


