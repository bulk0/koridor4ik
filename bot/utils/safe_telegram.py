from __future__ import annotations

import asyncio
from typing import Optional
from aiogram.types import Message
from aiogram.enums import ChatAction

DEFAULT_TIMEOUT = 40

async def safe_answer(message: Message, text: str, *, attempts: int = 3, **kwargs) -> Optional[Message]:
	"""
	Отправка сообщения с ретраями и увеличенным timeout.
	"""
	last_exc: Exception | None = None
	for i in range(attempts):
		try:
			return await message.answer(text, request_timeout=kwargs.pop("request_timeout", DEFAULT_TIMEOUT), **kwargs)
		except Exception as e:
			last_exc = e
			await asyncio.sleep(min(1 + i, 3))
	return None

async def safe_edit(message_obj: Message, text: str, *, attempts: int = 3, **kwargs) -> bool:
	last_exc: Exception | None = None
	for i in range(attempts):
		try:
			await message_obj.edit_text(text, **kwargs)
			return True
		except Exception as e:
			last_exc = e
			await asyncio.sleep(min(1 + i, 3))
	return False

async def safe_typing(message: Message) -> None:
	try:
		await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
	except Exception:
		pass


