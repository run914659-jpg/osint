"""Update middlewares (PLAN.md §3 middleware order + Phases 3/5).

Registered order on ``dp.message`` / ``dp.callback_query`` (first registered
= outermost):

1. :class:`DbMiddleware`      — injects the shared aiosqlite connection as
                                ``data["db"]`` for every handler.
2. :class:`UserUpsertMiddleware` — best-effort ``users`` row upsert on any
                                message / callback (never for join requests).
3. :class:`ForceJoinGateMiddleware` — non-admins with pending channels get
                                the force-join screen and the handler chain
                                is cancelled.

Admin guard (PLAN item #4, "AdminCheckMiddleware"): implemented as
:class:`AdminGuardMiddleware` attached to the *admin router's* observers
(see ``main.create_dispatcher``). aiogram v3 has no per-handler flag channel
into middleware ``data`` (middleware runs after filters matched, and the
chosen handler is not exposed), but router-attached middlewares only run when
a handler **of that router** matched — which structurally guarantees EVERY
admin handler is guarded without per-handler boilerplate.

Cancellation note (aiogram 3.15 quirk): raising the legacy
``CancelHandler`` escapes ``feed_update`` as an *uncaught* exception (only
``SkipHandler`` is handled inside ``TelegramEventObserver.trigger``, and
``SkipHandler`` would fall through to the *next* matching handler — wrong for
a gate). The clean aiogram-3 way to cancel is to simply **not call the
wrapped handler**: propagation stops, the update counts as handled, and no
error is logged. All cancellations below use that pattern.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from osintbot.db import get_pending_channels, upsert_user

__all__ = [
    "DbMiddleware",
    "UserUpsertMiddleware",
    "ForceJoinGateMiddleware",
    "AdminGuardMiddleware",
]

# Callbacks that always pass the force-join gate (user must be able to
# verify, refresh the menu, or open a channel while blocked).
GATE_WHITELIST_PREFIXES: tuple[str, ...] = ("vj:", "ch:", "back:")

# First word of the /start command (handles /start, /start payload).
GATE_WHITELIST_TEXT_PREFIX = "/start"

ADMIN_ONLY_ALERT = "⛔ Admins only"

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DbMiddleware(BaseMiddleware):
    """Bind the single shared aiosqlite connection into handler ``data``."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict) -> Any:
        data["db"] = self.conn
        return await handler(event, data)


class UserUpsertMiddleware(BaseMiddleware):
    """Upsert the ``users`` row for message + callback_query events.

    Skips bots and (defensively) chat_join_request events. Failures are
    swallowed — tracking must never break real handling.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict) -> Any:
        conn = data.get("db")
        if conn is not None and isinstance(event, (Message, CallbackQuery)):
            user = getattr(event, "from_user", None)
            if user is not None and not user.is_bot:
                try:
                    await upsert_user(conn, user.id, user.username, user.full_name)
                except Exception:  # noqa: BLE001 - best-effort tracking
                    pass
        return await handler(event, data)


class ForceJoinGateMiddleware(BaseMiddleware):
    """Block non-admin users who still owe memberships (PLAN R5 / Phase 5).

    On a gated message/callback the force-join screen is shown and the
    handler chain is cancelled (see module docstring for why we do not raise
    ``CancelHandler``). Whitelisted: ``/start`` text, callbacks starting
    with ``vj:``, ``ch:`` or ``back:``.
    """

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = set(admin_ids)

    @staticmethod
    def _is_whitelisted(event: TelegramObject) -> bool:
        if isinstance(event, CallbackQuery):
            data = event.data or ""
            return data.startswith(GATE_WHITELIST_PREFIXES)
        if isinstance(event, Message):
            text = event.text or ""
            return text.startswith(GATE_WHITELIST_TEXT_PREFIX)
        return False

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.id in self.admin_ids or self._is_whitelisted(event):
            return await handler(event, data)

        conn = data.get("db")
        bot = data.get("bot")
        if conn is None or bot is None:
            # Without db/bot we cannot gate — fail open rather than break.
            return await handler(event, data)

        try:
            pending = await get_pending_channels(conn, user.id)
        except Exception:  # noqa: BLE001 - a DB hiccup must not lock everyone out
            return await handler(event, data)
        if not pending:
            return await handler(event, data)

        # Lazy import: handlers.start owns the screen builders; importing at
        # call time keeps import order flexible (handlers never import this
        # module, but a future wave might).
        from osintbot.handlers.start import show_force_join_screen

        try:
            await show_force_join_screen(event, bot, conn, user.id)
        except Exception:  # noqa: BLE001 - e.g. user blocked the bot
            pass
        if isinstance(event, CallbackQuery):
            # Clear the button "loading" spinner even though we cancel.
            try:
                await event.answer()
            except Exception:  # noqa: BLE001 - stale query ids
                pass
        return None  # cancelled: the wrapped handler is intentionally not called


class AdminGuardMiddleware(BaseMiddleware):
    """Reject non-admins reaching the admin router (PLAN R1).

    Attach to ``admin_router.callback_query`` and
    ``admin_router.message`` (see ``main.create_dispatcher``): it only runs
    when a handler of that router already matched, so every admin handler is
    covered by construction. Non-admins get the ``⛔ Admins only`` alert.
    """

    def __init__(self, admin_ids: set[int]) -> None:
        self.admin_ids = set(admin_ids)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and user.id in self.admin_ids:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            try:
                await event.answer(ADMIN_ONLY_ALERT, show_alert=True)
            except Exception:  # noqa: BLE001 - answer may fail on stale queries
                pass
        elif isinstance(event, Message):
            try:
                await event.answer(ADMIN_ONLY_ALERT)
            except Exception:  # noqa: BLE001
                pass
        return None  # cancelled
