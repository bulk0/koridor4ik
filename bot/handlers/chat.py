from __future__ import annotations

import asyncio
from aiogram import Router
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.async_llm import AsyncLLMClient
from ..services.persona_search import PersonaSearchService
from ..services.logger import ensure_session_files, append_question, append_answer, export_answers_file, log_event
from ..keyboards import chat_controls_kb
from chat.talk import build_prompt, Persona

router = Router()
_search = PersonaSearchService()

async def _select_by_phrase(candidates: list[tuple[str, str]], phrase: str) -> list[tuple[str, str]]:
	phrase_l = phrase.lower()
	res = []
	for pid, title in candidates:
		if all(tok in (title.lower()) for tok in phrase_l.split() if len(tok) >= 3):
			res.append((pid, title))
	return res[:5]

@router.message(DialogStates.nl_candidates)
@router.message(DialogStates.filter_candidates)
async def choose_candidates(message: Message, state: FSMContext) -> None:
	data = await state.get_data()
	list_key = "nl_personas" if "nl_personas" in data else "fl_personas"
	candidates: list[tuple[str, str]] = data.get(list_key, [])
	if not candidates:
		await message.answer("Список кандидатов пуст. Вернитесь к началу.")
		return
	# Парсим выбор
	text = (message.text or "").strip()
	chosen: list[tuple[str, str]] = []
	parts = [p.strip() for p in text.split(",") if p.strip()]
	by_idx = {str(i): candidates[i - 1] for i in range(1, len(candidates) + 1)}
	for token in parts:
		if token in by_idx:
			chosen.append(by_idx[token])
		elif "-" in token and all(t.strip().isdigit() for t in token.split("-", 1)):
			try:
				a, b = token.split("-", 1)
				ai, bi = int(a), int(b)
				for i in range(ai, bi + 1):
					s = str(i)
					if s in by_idx:
						chosen.append(by_idx[s])
			except Exception:
				pass
	if not chosen and any(ch.isalpha() for ch in text):
		chosen = await _select_by_phrase(candidates, text)
	if not chosen:
		await message.answer("Не понял выбор. Укажите индексы (например, 1,3-5) или краткое описание.")
		return
	# Переходим в чат
	await state.update_data(chosen=chosen)
	await state.set_state(DialogStates.chat)
	await message.answer(
		"Введите вопрос. Примеры:\n"
		"- Каким ИИ‑сервисом вы чаще пользуетесь и почему?\n"
		"- Как вы ищете информацию: через ИИ или поисковик?\n",
		reply_markup=chat_controls_kb(),
	)

@router.message(DialogStates.chat)
async def chat_ask(message: Message, state: FSMContext) -> None:
	question = (message.text or "").strip()
	if not question:
		await message.answer("Пустой вопрос. Напишите вопрос.")
		return
	data = await state.get_data()
	chosen: list[tuple[str, str]] = data.get("chosen", [])
	if not chosen:
		await message.answer("Собеседники не выбраны.")
		return
	user_id = message.from_user.id if message.from_user else 0
	session = ensure_session_files(user_id)
	append_question(session, question)
	llm = AsyncLLMClient()
	# Получаем профили синхронно в пуле (чтобы собрать build_prompt)
	from chat.talk import conn
	def _load_profiles(ids: list[str]) -> list[Persona]:
		with conn() as c:
			rows = c.execute(
				"SELECT persona_id, title, profile_md FROM personas WHERE persona_id IN (%s)" % (",".join(["?"] * len(ids))),
				ids,
			).fetchall()
		return [Persona(*row) for row in rows]
	persona_ids = [pid for pid, _ in chosen]
	personas = await asyncio.to_thread(_load_profiles, persona_ids)
	tasks = []
	for p in personas:
		system, user = build_prompt(p.profile_md, question)
		tasks.append(llm.chat(system=system, user=user, temperature=1.0))
	results = await asyncio.gather(*tasks, return_exceptions=True)
	collected = []
	for p, r in zip(personas, results):
		if isinstance(r, Exception):
			answer = f"(ошибка ответа: {r})"
			log_event(user_id, "auto", "answer_error", persona=p.title, error=str(r))
		else:
			answer = str(r)
		append_answer(session, p.title, answer)
		await message.answer(f"— {p.title} —\n{answer}")
		collected.append({"title": p.title, "answer": answer})
	await state.update_data(last_question=question, last_answers=collected)

@router.callback_query(lambda c: c.data in {"chat:export_answers", "chat:finish"})
async def chat_controls(callback: CallbackQuery, state: FSMContext) -> None:
	data = await state.get_data()
	user_id = callback.from_user.id if callback.from_user else 0
	session = ensure_session_files(user_id)
	if callback.data == "chat:export_answers":
		question = data.get("last_question", "")
		answers = data.get("last_answers", [])
		path = export_answers_file(session, question, answers)
		await callback.message.answer_document(FSInputFile(str(path)), caption="Ответы выгружены.")
		log_event(user_id, "auto", "export_answers", path=str(path))
	else:
		await callback.message.answer("Спасибо, всего доброго.\nНажмите, чтобы выгрузить лог беседы:")
		await callback.message.answer_document(FSInputFile(str(session.summary_md)))
		await state.set_state(DialogStates.ending)
		log_event(user_id, "auto", "export_log", path=str(session.summary_md))
	await callback.answer()


