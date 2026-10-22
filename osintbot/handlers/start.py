"""/start, main menu and the force-join verify flow (PLAN Phases 3 + 5).

Keyboard layout rules (enforced by ``keyboards.py``): feature rows are
2-up, Back/Verify always own their own full-width row, Back is last.

The ``t:<code>`` tool handlers are intentionally NOT registered here —
a later wave (WAVE3) adds ``handlers/tools/``.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

from osintbot import texts, toolspec
from osintbot.db import (
    get_pending_channels,
    get_setting,
    list_channels,
    set_setting,
    set_verified,
)
from osintbot.keyboards import PRIMARY, SUCCESS, btn, full, make_kb, row2

__all__ = [
    "router",
    "is_joined",
    "build_menu_kb",
    "build_force_join_kb",
    "show_force_join_screen",
    "edit_or_send",
]

router = Router(name="start")

_VERIFY_FAILS_KEY = "vj_unverifiable:{user_id}"
_GRACE_TAPS = 2  # unverifiable channels let the user through after 2 taps

_TOOL_ITEMS = [
    btn(f"{t.emoji} {t.title}", cb=f"t:{t.code}", style=PRIMARY) for t in toolspec.TOOLS
]


# ------------------------------------------------------------- keyboards ----

def _chunk2(items: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Chunk buttons 2-up; a lone leftover becomes its own (still not tail) row."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(items), 2):
        chunk = items[i : i + 2]
        rows.append(row2(*chunk) if len(chunk) == 2 else full(*chunk))
    return rows


def _channel_button(ch: dict) -> InlineKeyboardButton:
    icon = "📩" if ch.get("join_mode") == "request" else "🔗"
    label = str(ch.get("title") or ch.get("username") or ch.get("chat_id"))[:48]
    url = ch.get("invite_url")
    if not url and ch.get("username"):
        url = f"https://t.me/{ch['username']}"
    if url:
        return btn(f"{icon} {label}", url=url)
    return btn(f"{icon} {label}", disabled=True)


def build_menu_kb(channels: list[dict], *, is_admin: bool) -> InlineKeyboardMarkup:
    """Welcome-screen keyboard: channels + Verify + 16 tools (+ admin) + Back."""
    rows: list[list[InlineKeyboardButton]] = []

    channel_buttons = [_channel_button(ch) for ch in channels]
    rows.extend(_chunk2(channel_buttons))
    if channel_buttons:
        rows.append(full(btn("✅ I Joined / Verify", cb="vj:check", style=SUCCESS)))

    rows.extend(_chunk2(list(_TOOL_ITEMS)))

    if is_admin:
        rows.append(full(btn("🔧 Channel Manage", cb="adm:menu", style=PRIMARY)))

    rows.append(full(btn("◀ Back", cb="back:menu", style=PRIMARY)))
    return make_kb(*rows)


def build_force_join_kb(pending: list[dict]) -> InlineKeyboardMarkup:
    """Force-join screen: one join button per pending channel + Verify tail."""
    rows = _chunk2([_channel_button(ch) for ch in pending])
    rows.append(full(btn("✅ I Joined / Verify", cb="vj:check", style=SUCCESS)))
    return make_kb(*rows)


# --------------------------------------------------------------- helpers ----

def is_joined(member) -> bool:
    """Membership predicate for a getChatMember result (PLAN Phase 5.2)."""
    status = getattr(member, "status", None)
    if status in ("member", "administrator", "creator"):
        return True
    if status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


def _title_of(ch: dict) -> str:
    return str(ch.get("title") or ch.get("username") or ch.get("chat_id"))


async def edit_or_send(
    callback: CallbackQuery, text: str, kb: InlineKeyboardMarkup
) -> None:
    """Edit the tapped message; fall back to a fresh DM on any Telegram error.

    ``message is not modified`` is swallowed silently (PLAN §6).
    """
    msg = callback.message
    if msg is not None and getattr(msg, "edit_text", None) is not None:
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
        except TelegramForbiddenError:
            pass
    await callback.bot.send_message(
        callback.from_user.id, text, reply_markup=kb, parse_mode="HTML"
    )


async def show_force_join_screen(
    event: TelegramObject, bot, conn, user_id: int
) -> None:
    """Render the force-join gate for any gated event (message or callback)."""
    pending = await get_pending_channels(conn, user_id)
    if not pending:
        return
    text = texts.FORCE_JOIN(pending)
    kb = build_force_join_kb(pending)
    if isinstance(event, CallbackQuery):
        await edit_or_send(event, text, kb)
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


async def _show_welcome_or_gate(event: TelegramObject, conn, config) -> None:
    """Send (message) or edit (callback) welcome+menu, gate first if pending."""
    user_id = event.from_user.id
    pending = await get_pending_channels(conn, user_id)
    if pending:
        text = texts.FORCE_JOIN(pending)
        kb = build_force_join_kb(pending)
    else:
        channels = await list_channels(conn)
        text = texts.WELCOME
        kb = build_menu_kb(channels, is_admin=user_id in config.admin_ids)
    if isinstance(event, CallbackQuery):
        await edit_or_send(event, text, kb)
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


# ------------------------------------------------------------- handlers -----

@router.message(Command("start"))
async def cmd_start(message: Message, db, config) -> None:
    """``db`` is the shared aiosqlite conn (DbMiddleware), ``config`` the Config."""
    await _show_welcome_or_gate(message, db, config)


@router.callback_query(F.data == "back:menu")
async def cb_back_menu(callback: CallbackQuery, bot, db, config) -> None:
    await _show_welcome_or_gate(callback, db, config)
    await callback.answer()


@router.callback_query(F.data == "vj:check")
async def cb_verify(callback: CallbackQuery, bot, db, config) -> None:
    user_id = callback.from_user.id
    pending = await get_pending_channels(db, user_id)

    still: list[dict] = []
    unverifiable: list[dict] = []
    for ch in pending:
        try:
            member = await bot.get_chat_member(ch["chat_id"], user_id)
            if is_joined(member):
                await set_verified(db, user_id, ch["chat_id"])
            else:
                still.append(ch)
        except (TelegramBadRequest, TelegramForbiddenError):
            # Bot lost admin rights / chat inaccessible → cannot verify.
            unverifiable.append(ch)

    fails = 0
    if unverifiable:
        key = _VERIFY_FAILS_KEY.format(user_id=user_id)
        raw = await get_setting(db, key, "0")
        fails = (int(raw) if raw and raw.isdigit() else 0) + 1
        await set_setting(db, key, str(fails))

    if still or (unverifiable and fails < _GRACE_TAPS):
        remaining = still + unverifiable
        text = texts.FORCE_JOIN(remaining)
        await edit_or_send(callback, text, build_force_join_kb(remaining))
        titles = ", ".join(_title_of(ch) for ch in remaining)[:150]
        await callback.answer(f"Still pending: {titles}", show_alert=True)
        return

    # All joined (or grace exhausted on unverifiable channels).
    if unverifiable:
        await callback.answer(
            f"⚠️ {len(unverifiable)} channel(s) can't be checked — proceeding anyway",
            show_alert=True,
        )
    else:
        await callback.answer("✅ Verified")
    await _show_welcome_or_gate(callback, db, config)
