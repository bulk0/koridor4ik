from __future__ import annotations

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiogram.types import FSInputFile

from .config import BotConfig
from .handlers.start import router as start_router
from .handlers.nl_search import router as nl_router
from .handlers.filter_search import router as filter_router
from .handlers.chat import router as chat_router
from .handlers.candidates import router as candidates_router

_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _level, logging.INFO))
logger = logging.getLogger("bot")

async def _setup_dp() -> Dispatcher:
	dp = Dispatcher(storage=MemoryStorage())
	dp.include_router(start_router)
	dp.include_router(nl_router)
	dp.include_router(filter_router)
	dp.include_router(candidates_router)
	dp.include_router(chat_router)
	return dp

async def run_polling() -> None:
	cfg = BotConfig.from_env()
	cfg.validate()
	bot = Bot(token=cfg.bot_token)
	dp = await _setup_dp()
	await dp.start_polling(bot)

async def run_webhook() -> None:
	cfg = BotConfig.from_env()
	cfg.validate()
	bot = Bot(token=cfg.bot_token)
	dp = await _setup_dp()
	app = web.Application()
	# health endpoint
	async def health_handler(request):
		return web.Response(text="ok")
	app.router.add_get("/health", health_handler)
	# debug: DNS and outbound checks
	async def dns_handler(request):
		import socket
		try:
			addrs = socket.getaddrinfo("api.telegram.org", 443, proto=socket.IPPROTO_TCP)
			items = [{"family": a[0], "socktype": a[1], "proto": a[2], "addr": a[4]} for a in addrs]
			return web.json_response({"ok": True, "api.telegram.org": items})
		except Exception as e:
			return web.json_response({"ok": False, "error": str(e)}, status=500)
	app.router.add_get("/debug/dns", dns_handler)
	async def httpbin_handler(request):
		import aiohttp, asyncio
		try:
			timeout = aiohttp.ClientTimeout(total=8)
			async with aiohttp.ClientSession(timeout=timeout) as s:
				async with s.get("https://httpbin.org/get") as r:
					txt = await r.text()
			return web.Response(text=txt, content_type="application/json")
		except Exception as e:
			return web.json_response({"ok": False, "error": str(e)}, status=500)
	app.router.add_get("/debug/http", httpbin_handler)
	async def getme_handler(request):
		import aiohttp
		try:
			timeout = aiohttp.ClientTimeout(total=8)
			url = f"https://api.telegram.org/bot{cfg.bot_token}/getMe"
			async with aiohttp.ClientSession(timeout=timeout) as s:
				async with s.get(url) as r:
					txt = await r.text()
			return web.Response(text=txt, content_type="application/json")
		except Exception as e:
			return web.json_response({"ok": False, "error": str(e)}, status=500)
	app.router.add_get("/debug/getme", getme_handler)
	webhook_path = f"/{cfg.webhook_secret_path}"
	SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
	setup_application(app, dp, bot=bot)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "0.0.0.0", 8080)
	await site.start()
	logger.info("Webhook started on 0.0.0.0:8080")
	# Устанавливаем вебхук после старта сервера; ошибки не фатальны (сервис остаётся healthy)
	try:
		url = f"{cfg.webhook_base_url.rstrip('/')}{webhook_path}"
		if cfg.webhook_self_signed_cert_path and cfg.webhook_self_signed_cert_path.exists():
			cert = FSInputFile(str(cfg.webhook_self_signed_cert_path))
			await bot.set_webhook(url=url, certificate=cert)
		else:
			await bot.set_webhook(url=url)
		logger.info("Webhook set to %s", url)
	except Exception as e:
		logger.warning("Failed to set webhook: %s", e)
	while True:
		await asyncio.sleep(3600)

def main() -> None:
	cfg = BotConfig.from_env()
	if cfg.mode == "webhook":
		asyncio.run(run_webhook())
	else:
		asyncio.run(run_polling())

if __name__ == "__main__":
	main()


