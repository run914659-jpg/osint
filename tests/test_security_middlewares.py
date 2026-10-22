"""Security middleware + webhook secret tests (REVIEW M6). Fully offline.

No network anywhere: a dummy-token Bot gets a stub session (canned results,
recorded API calls) and updates are fed through ``create_dispatcher`` — the
exact wiring ``main.py`` uses — plus aiohttp's in-process TestClient for the
webhook routes.

aiogram forbids attaching the singleton routers to two Dispatchers, so the
dispatcher + DB are built ONCE on a module-scoped event loop and each test
starts by wiping the tables (every test seeds exactly what it needs).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetChatMember,
    SendMessage,
)
from aiogram.types import (
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    Message,
    Update,
    User,
)

from osintbot import db
from osintbot.config import Config
from osintbot.main import WEBHOOK_SECRET_HEADER, create_dispatcher, create_web_app

# Applied per async test (not module-level ``pytestmark``) so the five sync
# config tests below don't get the asyncio mark and warn.
_aio = pytest.mark.asyncio(loop_scope="module")

ADMIN_ID = 1000
USER_ID = 500
CHANNEL_ID = -100111
CHAT_ID = 777

WEBHOOK_SECRET = "X" * 48  # satisfies the >=32-char webhook rule (H3)


# ------------------------------------------------------------------ stubs ----

class _StubSession:
    """Offline aiogram session: records API methods, returns canned results.

    ``member_status`` / ``fail_get_member`` control ``get_chat_member`` so the
    verify flow (H1/H2/M2) can be driven without touching Telegram.
    """

    def __init__(self) -> None:
        self.calls: list = []
        self.member_status: str = "member"  # "member" = joined, else = left
        self.fail_get_member: bool = False

    async def __call__(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetChatMember):
            if self.fail_get_member:
                raise TelegramBadRequest(
                    method=method, message="Bad Request: chat not found"
                )
            user = User(id=method.user_id, is_bot=False, first_name="T")
            if self.member_status == "member":
                return ChatMemberMember(user=user, status="member")
            return ChatMemberLeft(user=user, status="left")
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.calls),
                date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True  # edits, callback answers, etc.

    async def close(self) -> None:
        pass

    def of(self, kind) -> list:
        return [m for m in self.calls if isinstance(m, kind)]

    def texts(self) -> list[str]:
        return [m.text for m in self.calls if getattr(m, "text", None)]


class _StubDispatcher:
    """Stand-in Dispatcher for the webhook route tests."""

    def __init__(self, error: Exception | None = None) -> None:
        self.fed: list[Update] = []
        self.error = error

    async def feed_update(self, bot, update) -> None:
        self.fed.append(update)
        if self.error is not None:
            raise self.error


def _make_bot() -> Bot:
    bot = Bot("123456:TESTTOKEN")  # dummy token — session never hits the network
    bot.session = _StubSession()
    return bot


def _cfg(**kwargs) -> Config:
    return Config(bot_token="123456:TESTTOKEN", admin_ids={ADMIN_ID}, **kwargs)


async def _seed_gate_channel(conn) -> None:
    """One must-join channel — every user below is gated on it."""
    await db.add_channel(
        conn,
        chat_id=CHANNEL_ID,
        username="gatechan",
        title="Gate Chan",
        type="channel",
        join_mode="direct",
        invite_url="https://t.me/gatechan",
        created_by=ADMIN_ID,
    )


# --------------------------------------------------------------- fixtures ----

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def env(tmp_path_factory):
    """One shared (db-conn, bot, dispatcher) trio for the whole module."""
    path = str(tmp_path_factory.mktemp("sec") / "security.db")
    await db.init_db(path)
    conn = await db.get_conn(path)
    bot = _make_bot()
    dp = create_dispatcher(_cfg(), bot, conn)
    yield SimpleNamespace(conn=conn, bot=bot, dp=dp)
    await conn.close()


async def _reset(env) -> None:
    for table in ("users", "user_channels", "channels", "settings"):
        await env.conn.execute(f"DELETE FROM {table}")
    await env.conn.commit()
    env.bot.session.calls.clear()
    env.bot.session.member_status = "member"
    env.bot.session.fail_get_member = False


# ------------------------------------------------------- crafted updates ----

def _cb_update(update_id: int, data: str, user_id: int, chat_type: str = "private") -> Update:
    return Update.model_validate({
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "chat_instance": "ci",
            "data": data,
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "message": {
                "message_id": 42,
                "date": 1700000000,
                "chat": {"id": CHAT_ID, "type": chat_type},
                "from": {"id": 123456, "is_bot": True, "first_name": "Bot"},
                "text": "menu",
            },
        },
    })


def _msg_update(update_id: int, text: str, user_id: int, chat_type: str = "private") -> Update:
    return Update.model_validate({
        "update_id": update_id,
        "message": {
            "message_id": 43,
            "date": 1700000000,
            "chat": {"id": CHAT_ID if chat_type == "private" else -100555,
                     "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    })


# ======================================================= ForceJoinGate ======

@_aio
async def test_gate_blocks_tool_callback_for_pending_user(env):
    """Non-admin with a pending channel: handler NOT invoked, force-join shown."""
    await _reset(env)
    await _seed_gate_channel(env.conn)

    await env.dp.feed_update(env.bot, _cb_update(1, "t:num", USER_ID))

    texts = env.bot.session.texts()
    assert any("Join required" in t for t in texts), "force-join screen must be shown"
    assert not any("Number → Info" in t for t in texts), "handler must NOT run"
    edits = env.bot.session.of(EditMessageText)
    assert edits, "the force-join screen replaces the tapped message"
    rows = edits[0].reply_markup.inline_keyboard
    assert rows[-1][0].callback_data == "vj:check"  # Verify owns the last row


@_aio
async def test_gate_whitelisted_start_reaches_handler(env, monkeypatch):
    """``/start`` is whitelisted: cmd_start runs (shows its own gate screen)."""
    import osintbot.handlers.start as start_mod

    await _reset(env)
    await _seed_gate_channel(env.conn)

    called: list = []

    async def fake_show(event, conn, config):
        called.append(event)

    monkeypatch.setattr(start_mod, "_show_welcome_or_gate", fake_show)
    await env.dp.feed_update(env.bot, _msg_update(2, "/start", USER_ID))

    assert len(called) == 1, "cmd_start must run for the whitelisted /start"


@_aio
async def test_gate_admin_passes_through(env):
    """Admins are never gated — the tool prompt must render for them."""
    await _reset(env)
    await _seed_gate_channel(env.conn)

    await env.dp.feed_update(env.bot, _cb_update(3, "t:num", ADMIN_ID))

    texts = env.bot.session.texts()
    assert any("Number → Info" in t for t in texts)
    assert not any("Join required" in t for t in texts)


@_aio
async def test_gate_sends_nothing_in_group_chats(env):
    """M3: group/supergroup updates are skipped entirely — no messages sent."""
    await _reset(env)
    await _seed_gate_channel(env.conn)

    await env.dp.feed_update(env.bot, _msg_update(4, "/start", USER_ID, "supergroup"))
    await env.dp.feed_update(env.bot, _msg_update(5, "hello", USER_ID, "group"))

    assert env.bot.session.calls == [], (
        "the gate must stay silent outside private chats"
    )


@_aio
async def test_gate_skips_events_without_pending_channels(env):
    """No channels saved → everyone passes straight to the handlers."""
    await _reset(env)

    await env.dp.feed_update(env.bot, _cb_update(6, "t:num", USER_ID))

    assert any("Number → Info" in t for t in env.bot.session.texts())


# ======================================================== AdminGuard ========

@_aio
async def test_admin_guard_rejects_non_admin_callback(env):
    """Non-admin ``adm:menu``: alert answered, handler NOT executed."""
    await _reset(env)
    await _seed_gate_channel(env.conn)
    # Verified user → the force-join gate passes them through, so this test
    # exercises the AdminGuard itself (not the gate, covered above).
    await db.set_verified(env.conn, USER_ID, CHANNEL_ID)

    await env.dp.feed_update(env.bot, _cb_update(7, "adm:menu", USER_ID))

    alerts = [m for m in env.bot.session.of(AnswerCallbackQuery) if m.show_alert]
    assert len(alerts) == 1 and "Admins only" in alerts[0].text
    assert env.bot.session.of(EditMessageText) == [], "admin handler must NOT run"


@_aio
async def test_admin_guard_allows_admin_callback(env):
    """Admin ``adm:menu`` renders the admin panel."""
    await _reset(env)
    await _seed_gate_channel(env.conn)

    await env.dp.feed_update(env.bot, _cb_update(8, "adm:menu", ADMIN_ID))

    edits = env.bot.session.of(EditMessageText)
    assert edits and "Admin Panel" in edits[0].text


# ================================================ Verify flow (H1/H2/M8) ===

@_aio
async def test_verify_revokes_stale_cache_when_user_left(env):
    """H2: verify → user leaves → tapping Verify revokes the cached row."""
    await _reset(env)
    await _seed_gate_channel(env.conn)
    await db.set_verified(env.conn, USER_ID, CHANNEL_ID)  # cached from before
    env.bot.session.member_status = "left"

    await env.dp.feed_update(env.bot, _cb_update(9, "vj:check", USER_ID))

    assert not await db.is_verified(env.conn, USER_ID, CHANNEL_ID), (
        "stale membership cache must be revoked (join → verify → leave → bypass)"
    )
    alerts = [m for m in env.bot.session.of(AnswerCallbackQuery) if m.show_alert]
    assert alerts and "Still pending" in alerts[0].text


@_aio
async def test_verify_joined_user_passes_and_gets_menu(env):
    """H2 positive path: joined → cache set, ✅ alert, welcome menu rendered."""
    await _reset(env)
    await _seed_gate_channel(env.conn)

    await env.dp.feed_update(env.bot, _cb_update(10, "vj:check", USER_ID))

    assert await db.is_verified(env.conn, USER_ID, CHANNEL_ID)
    answers = [m.text for m in env.bot.session.of(AnswerCallbackQuery)]
    assert any("Verified" in (t or "") for t in answers)
    edits = env.bot.session.of(EditMessageText)
    assert edits, "the welcome menu must be rendered after verification"
    assert "Join required" not in edits[-1].text, "gate screen must be gone"


@_aio
async def test_verify_unverifiable_channel_grace_then_bypass(env):
    """M2+H1: API failures grant a 2-tap grace, then persist the bypass."""
    await _reset(env)
    await _seed_gate_channel(env.conn)
    env.bot.session.fail_get_member = True

    # Tap 1 — within grace: still pending, no cache row written.
    await env.dp.feed_update(env.bot, _cb_update(11, "vj:check", USER_ID))
    assert not await db.is_verified(env.conn, USER_ID, CHANNEL_ID)
    alerts = [m for m in env.bot.session.of(AnswerCallbackQuery) if m.show_alert]
    assert alerts and "Still pending" in alerts[0].text

    # Tap 2 — grace exhausted: bypass persisted, counter reset, menu shown.
    env.bot.session.calls.clear()
    await env.dp.feed_update(env.bot, _cb_update(12, "vj:check", USER_ID))
    assert await db.is_verified(env.conn, USER_ID, CHANNEL_ID), (
        "bypass must be persisted after grace exhaustion (H1/L5)"
    )
    answers = [m.text for m in env.bot.session.of(AnswerCallbackQuery) if m.text]
    assert any("proceeding anyway" in t for t in answers)
    fails = await db.get_setting(env.conn, "vj_unverifiable:500", "0")
    assert fails == "0", "fail counter must reset after the bypass"


@_aio
async def test_back_menu_clears_armed_tool_state(env):
    """M8: Back from a tool prompt clears the FSM — the next text is ignored."""
    await _reset(env)

    # Arm ToolFlow.waiting_query via the tool prompt card.
    await env.dp.feed_update(env.bot, _cb_update(13, "t:num", USER_ID))
    ctx = env.dp.fsm.get_context(bot=env.bot, chat_id=CHAT_ID, user_id=USER_ID)
    assert await ctx.get_state() is not None, "prompt must arm the tool flow"

    # Back to the menu — the state must be gone.
    await env.dp.feed_update(env.bot, _cb_update(14, "back:menu", USER_ID))
    assert await ctx.get_state() is None, "Back must clear the armed state"

    # A stray text after Back must NOT be swallowed as a tool query.
    env.bot.session.calls.clear()
    await env.dp.feed_update(env.bot, _msg_update(15, "hello", USER_ID))
    assert env.bot.session.calls == [], "no tool query must run after Back"


# =========================================================== Webhook ========

_UPDATE_PAYLOAD = {
    "update_id": 42,
    "message": {
        "message_id": 1,
        "date": 1700000000,
        "chat": {"id": 5, "type": "private"},
        "from": {"id": 5, "is_bot": False, "first_name": "T"},
        "text": "/start",
    },
}


async def _post(app, payload=None, headers=None):
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post(
            "/webhook",
            data=json.dumps(payload if payload is not None else _UPDATE_PAYLOAD),
            headers=headers or {},
        )
        return resp.status
    finally:
        await client.close()


def _webhook_app(dp=None):
    cfg = _cfg(run_mode="webhook", webhook_secret=WEBHOOK_SECRET)
    return create_web_app(cfg, _make_bot(), dp or _StubDispatcher(), path="/webhook")


@_aio
async def test_webhook_rejects_missing_secret_header():
    assert await _post(_webhook_app()) == 403


@_aio
async def test_webhook_rejects_wrong_secret():
    status = await _post(
        _webhook_app(), headers={WEBHOOK_SECRET_HEADER: "wrong" * 10}
    )
    assert status == 403


@_aio
async def test_webhook_accepts_correct_secret_and_feeds_update():
    dp = _StubDispatcher()
    status = await _post(
        _webhook_app(dp), headers={WEBHOOK_SECRET_HEADER: WEBHOOK_SECRET}
    )
    assert status == 200
    assert len(dp.fed) == 1
    assert dp.fed[0].message.text == "/start"


@_aio
async def test_webhook_returns_200_even_when_handler_raises():
    """L6: the update is already accepted — a 500 would make Telegram retry."""
    dp = _StubDispatcher(error=RuntimeError("boom"))
    status = await _post(
        _webhook_app(dp), headers={WEBHOOK_SECRET_HEADER: WEBHOOK_SECRET}
    )
    assert status == 200


@_aio
async def test_webhook_rejects_malformed_json():
    client = TestClient(TestServer(_webhook_app()))
    await client.start_server()
    try:
        resp = await client.post(
            "/webhook",
            data="{not json",
            headers={WEBHOOK_SECRET_HEADER: WEBHOOK_SECRET},
        )
        assert resp.status == 400
    finally:
        await client.close()


@_aio
async def test_health_endpoint_ok():
    client = TestClient(TestServer(_webhook_app()))
    await client.start_server()
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        assert (await resp.json()) == {"ok": True}
    finally:
        await client.close()


# =================================================== Config webhook guard ===

def test_config_webhook_rejects_default_secret(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:T")
    monkeypatch.setenv("RUN_MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_SECRET", "change-me")
    with pytest.raises(SystemExit, match="WEBHOOK_SECRET"):
        Config.from_env(env_file=None)


def test_config_webhook_rejects_empty_secret(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:T")
    monkeypatch.setenv("RUN_MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    with pytest.raises(SystemExit, match="WEBHOOK_SECRET"):
        Config.from_env(env_file=None)


def test_config_webhook_rejects_short_secret(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:T")
    monkeypatch.setenv("RUN_MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_SECRET", "short-but-over-16-chars")
    with pytest.raises(SystemExit, match="32"):
        Config.from_env(env_file=None)


def test_config_webhook_accepts_random_secret(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:T")
    monkeypatch.setenv("RUN_MODE", "webhook")
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    cfg = Config.from_env(env_file=None)
    assert cfg.run_mode == "webhook"
    assert cfg.webhook_secret == WEBHOOK_SECRET


def test_config_polling_keeps_default_secret(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:T")
    monkeypatch.setenv("RUN_MODE", "polling")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    cfg = Config.from_env(env_file=None)
    assert cfg.run_mode == "polling"
    assert cfg.webhook_secret == "change-me"
