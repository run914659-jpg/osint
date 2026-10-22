"""Entrypoint (PLAN.md Phase 3 / R9).

Importing this module has NO side effects — every effect (config load, DB,
Bot, polling/webhook server) lives inside :func:`main`.

``create_dispatcher`` is factored out so the whole wiring (middlewares +
routers) can be smoke-tested without any network call.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from pydantic import ValidationError

from osintbot import texts
from osintbot.config import Config
from osintbot.db import get_conn, init_db
from osintbot.handlers import admin_router, all_routers
from osintbot.middlewares import (
    AdminGuardMiddleware,
    DbMiddleware,
    ForceJoinGateMiddleware,
    UserUpsertMiddleware,
)

logger = logging.getLogger("osintbot.main")

ALLOWED_UPDATES = ["message", "callback_query", "chat_join_request"]

WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _normalize_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


# ------------------------------------------------------------ dispatcher ----

def create_dispatcher(cfg: Config, bot: Bot, conn) -> Dispatcher:
    """Build the fully-wired Dispatcher — no network, safe to smoke-test.

    Middleware order (first registered = outermost) on message + callback:
    Db → UserUpsert → ForceJoinGate. ``DbMiddleware`` also fronts
    ``chat_join_request`` so the join-request handler gets its connection.
    The admin guard lives on the *admin router* (see middlewares.py for why).
    """
    dp = Dispatcher(storage=MemoryStorage(), config=cfg)

    db_mw = DbMiddleware(conn)
    upsert_mw = UserUpsertMiddleware()
    gate_mw = ForceJoinGateMiddleware(cfg.admin_ids)
    for observer in (dp.message, dp.callback_query):
        observer.middleware(db_mw)
        observer.middleware(upsert_mw)
        observer.middleware(gate_mw)
    dp.chat_join_request.middleware(db_mw)

    guard = AdminGuardMiddleware(cfg.admin_ids)
    admin_router.callback_query.middleware(guard)
    admin_router.message.middleware(guard)

    dp.include_routers(*all_routers())
    return dp


# ---------------------------------------------------------------- webhook ---

def create_web_app(
    cfg: Config, bot: Bot, dp: Dispatcher, *, path: str | None = None
) -> web.Application:
    """aiohttp app: ``POST <path>`` (secret-checked webhook) + ``GET /health``."""
    webhook_path = _normalize_path(path or cfg.webhook_path)
    app = web.Application()

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def webhook(request: web.Request) -> web.Response:
        if request.headers.get(WEBHOOK_SECRET_HEADER) != cfg.webhook_secret:
            return web.Response(status=403, text="forbidden")
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - malformed body
            return web.Response(status=400, text="invalid json")
        try:
            update = Update.model_validate(payload)
        except ValidationError:
            return web.Response(status=400, text="invalid update")
        try:
            await dp.feed_update(bot, update)
        except Exception:  # noqa: BLE001 - report failure to Telegram
            logger.exception("Error while processing webhook update")
            return web.Response(status=500, text="internal error")
        return web.Response(status=200)

    async def not_found(request: web.Request) -> web.Response:
        return web.json_response({"ok": False, "error": "not found"}, status=404)

    app.router.add_get("/health", health)
    app.router.add_post(webhook_path, webhook)
    app.router.add_route("*", "/{tail:.*}", not_found)
    return app


async def _run_webhook(cfg: Config, bot: Bot, dp: Dispatcher) -> None:
    path = _normalize_path(cfg.webhook_path)
    url = f"{cfg.public_base_url}{path}"
    await bot.set_webhook(
        url=url,
        secret_token=cfg.webhook_secret,
        allowed_updates=ALLOWED_UPDATES,
    )
    logger.info("Webhook registered: %s", url)

    app = create_web_app(cfg, bot, dp, path=path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=cfg.port)
    await site.start()
    logger.info("Listening on 0.0.0.0:%d", cfg.port)

    await notify_admins(bot, cfg)
    try:
        await asyncio.Event().wait()  # run until cancelled
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------- helpers ---

async def notify_admins(bot: Bot, cfg: Config) -> None:
    """Best-effort 'bot online' DM to every admin (never raises)."""
    for admin_id in cfg.admin_ids:
        with suppress(Exception):
            await bot.send_message(admin_id, texts.BOT_ONLINE, parse_mode="HTML")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.from_env()
    await init_db(cfg.db_path)
    conn = await get_conn(cfg.db_path)
    bot = Bot(cfg.bot_token)
    dp = create_dispatcher(cfg, bot, conn)
    try:
        if cfg.run_mode == "webhook":
            await _run_webhook(cfg, bot, dp)
        else:
            await notify_admins(bot, cfg)
            await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)
    finally:
        with suppress(Exception):
            await conn.close()
        with suppress(Exception):
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
