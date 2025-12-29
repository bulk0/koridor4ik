from __future__ import annotations

import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext

from ..states import DialogStates
from ..services.async_llm import AsyncLLMClient
from ..services.persona_search import PersonaSearchService
from ..services.logger import ensure_session_files, append_question, append_answer, export_answers_file, export_single_answer, log_event
from ..keyboards import chat_controls_kb, answer_kb, finish_kb
from ..utils.safe_telegram import safe_answer, safe_edit, safe_typing
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
	# используем один и тот же идентификатор сессии на протяжении диалога
	data_state = await state.get_data()
	existing_sid = data_state.get("session_id")
	if existing_sid:
		session = ensure_session_files(user_id, existing_sid)
	else:
		session = ensure_session_files(user_id)
		await state.update_data(session_id=session.session_id)
	append_question(session, question)
	llm = AsyncLLMClient()
	# Прогресс/typing
	status = await safe_answer(message, "Готовлю ответы… 0/?")
	import asyncio
	stop = asyncio.Event()
	async def typing_loop():
		while not stop.is_set():
			await safe_typing(message)
			await asyncio.sleep(4)
	typing_task = asyncio.create_task(typing_loop())
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
	collected = []
	done = 0
	total = len(personas)
	if status:
		await safe_edit(status, f"Готовлю ответы… {done}/{total}")
	async def one_answer(p: Persona):
		system, user = build_prompt(p.profile_md, question)
		try:
			txt = await llm.chat(system=system, user=user, temperature=1.0)
			return p, txt, None
		except Exception as e:
			return p, None, e
	for coro in asyncio.as_completed([one_answer(p) for p in personas]):
		p, txt, err = await coro
		if err:
			answer = f"(ошибка ответа: {err})"
			log_event(user_id, "auto", "answer_error", persona=p.title, error=str(err))
		else:
			answer = str(txt)
		append_answer(session, p.title, answer)
		idx = len(collected)
		await message.answer(f"— {p.title} —\n{answer}", reply_markup=answer_kb(idx))
		collected.append({"title": p.title, "answer": answer, "persona_id": p.persona_id})
		done += 1
		if status:
			await safe_edit(status, f"Готовлю ответы… {done}/{total}")
	stop.set()
	await typing_task
	await state.update_data(last_question=question, last_answers=collected)
	# Показать панель выгрузки после получения ответов
	if status:
		try:
			await status.edit_text(f"Готово. Получено ответов: {total}.", reply_markup=chat_controls_kb())
		except Exception:
			await message.answer("Готово. Вы можете выгрузить ответы или завершить диалог.", reply_markup=chat_controls_kb())
	# Показать панель управления уже после получения ответов
	await message.answer("Что дальше?", reply_markup=chat_controls_kb())

@router.callback_query(lambda c: c.data in {"chat:export_answers", "chat:export_session", "chat:finish"})
async def chat_controls(callback: CallbackQuery, state: FSMContext) -> None:
	data = await state.get_data()
	user_id = callback.from_user.id if callback.from_user else 0
	sid = data.get("session_id")
	session = ensure_session_files(user_id, sid) if sid else ensure_session_files(user_id)
	if callback.data == "chat:export_answers":
		question = data.get("last_question", "")
		answers = data.get("last_answers", [])
		path = export_answers_file(session, question, answers)
		await callback.message.answer_document(FSInputFile(str(path)), caption="Ответы выгружены.")
		log_event(user_id, "auto", "export_answers", path=str(path))
	elif callback.data == "chat:export_session":
		await callback.message.answer_document(FSInputFile(str(session.summary_md)), caption="Лог всей сессии выгружен.")
		log_event(user_id, "auto", "export_session", path=str(session.summary_md))
	else:
		await state.set_state(DialogStates.ending)
		await callback.message.answer("Завершили диалог. Что дальше?", reply_markup=finish_kb())
	await callback.answer()

@router.callback_query(F.data.startswith("ans:save:"))
async def save_single_answer(callback: CallbackQuery, state: FSMContext) -> None:
	data = await state.get_data()
	idx_str = callback.data.split(":", 2)[2]
	try:
		idx = int(idx_str)
	except Exception:
		await callback.answer("Не удалось сохранить")
		return
	answers = data.get("last_answers", [])
	if not (isinstance(answers, list) and 0 <= idx < len(answers)):
		await callback.answer("Ответ не найден")
		return
	item = answers[idx]
	question = data.get("last_question", "")
	user_id = callback.from_user.id if callback.from_user else 0
	sid = data.get("session_id")
	session = ensure_session_files(user_id, sid) if sid else ensure_session_files(user_id)
	path = export_single_answer(session, question, item["title"], item["answer"])
	await callback.message.answer_document(FSInputFile(str(path)), caption=f"Сохранён ответ: {item['title']}")
	await callback.answer("Сохранено")


