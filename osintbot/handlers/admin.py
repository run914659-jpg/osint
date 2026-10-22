"""Admin: channel management (PLAN Phase 4).

Flows: ``adm:menu`` → add (FSM) / list → detail → delete-confirm /
change-join-mode. Private channels pick ``request`` (creates_join_request
invite) or ``direct_private`` (exportChatInviteLink) via
``adm:ms:<chat_id>:<mode>``.

Every handler on this router is guarded by :class:`AdminGuardMiddleware`
attached to ``admin_router`` in ``main.create_dispatcher`` — non-admins get
``⛔ Admins only`` and the handler never runs (PLAN R1).
"""

from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from osintbot import texts
from osintbot.db import (
    add_channel,
    delete_channel,
    get_channel,
    list_channels,
    set_invite_url,
    set_join_mode,
)
from osintbot.handlers.start import edit_or_send
from osintbot.keyboards import DANGER, PRIMARY, SUCCESS, btn, cb, full, make_kb, row2

__all__ = ["router", "AddChannel", "parse_channel_target"]

router = Router(name="admin")


class AddChannel(StatesGroup):
    """FSM for the add-channel flow (PLAN Phase 4.2)."""

    waiting_for_target = State()
    waiting_for_choice = State()


# ------------------------------------------------------- pure parsing -------
# (unit-tested in tests/test_admin_parsing.py — keep this function free of
# aiogram / IO dependencies.)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_TG_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/(.*)$",
    re.IGNORECASE,
)


def parse_channel_target(raw: str | None) -> dict | None:
    """Parse a user-supplied channel reference.

    Accepts ``@username``, ``t.me/xxx``, ``https://t.me/xxx`` (incl. post
    links ``t.me/xxx/123``), invite links ``t.me/+hash``, ``t.me/joinchat/h``
    and ``+hash``, or a bare username. Returns
    ``{"kind": "username", "ref": "@name"}`` /
    ``{"kind": "invite", "ref": "https://t.me/+hash"}`` or ``None``.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s or any(c.isspace() for c in s):
        return None
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    m = _TG_URL_RE.match(s)
    if m:
        rest = m.group(1).strip("/")
        if not rest:
            return None
        first, _, tail = rest.partition("/")
        if first.startswith(("+", "~")):
            token = first[1:]
            return {"kind": "invite", "ref": f"https://t.me/+{token}"} if token else None
        if first.lower() == "joinchat":
            token = tail.split("/", 1)[0] if tail else ""
            return (
                {"kind": "invite", "ref": f"https://t.me/joinchat/{token}"}
                if token
                else None
            )
        if _USERNAME_RE.match(first):
            return {"kind": "username", "ref": "@" + first}
        return None

    if s.startswith(("+", "~")):
        token = s[1:]
        return {"kind": "invite", "ref": f"https://t.me/+{token}"} if token else None
    if s.startswith("@"):
        name = s[1:]
        return {"kind": "username", "ref": "@" + name} if _USERNAME_RE.match(name) else None
    if _USERNAME_RE.match(s):
        return {"kind": "username", "ref": "@" + s}
    return None


# ------------------------------------------------------------- keyboards ----

def _admin_menu_kb() -> InlineKeyboardMarkup:
    return make_kb(
        row2(
            btn("➕ Add Channel", cb="adm:add", style=PRIMARY),
            btn("📋 Channel List", cb="adm:list", style=PRIMARY),
        ),
        full(btn("◀ Back", cb="back:menu", style=PRIMARY)),
    )


def _saved_kb() -> InlineKeyboardMarkup:
    return make_kb(
        full(btn("📋 Channel List", cb="adm:list", style=PRIMARY)),
        full(btn("◀ Back", cb="back:admin", style=PRIMARY)),
    )


def _back_admin_kb() -> InlineKeyboardMarkup:
    return make_kb(full(btn("◀ Back", cb="back:admin", style=PRIMARY)))


def _add_prompt_kb() -> InlineKeyboardMarkup:
    return make_kb(full(btn("◀ Cancel", cb="back:admin", style=PRIMARY)))


def _channel_list_kb(channels: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list] = []
    items = [
        btn(
            str(ch.get("title") or ch.get("username") or ch["chat_id"])[:64],
            cb=cb("adm:ch", ch["chat_id"]),
            style=SUCCESS if ch.get("join_mode") in ("direct", "direct_private") else PRIMARY,
        )
        for ch in channels
    ]
    for i in range(0, len(items), 2):
        chunk = items[i : i + 2]
        rows.append(row2(*chunk) if len(chunk) == 2 else full(*chunk))
    rows.append(full(btn("➕ Add Channel", cb="adm:add", style=PRIMARY)))
    rows.append(full(btn("◀ Back", cb="back:admin", style=PRIMARY)))
    return make_kb(*rows)


def _detail_kb(chat_id: int) -> InlineKeyboardMarkup:
    return make_kb(
        row2(
            btn("🗑 Delete", cb=cb("adm:del", chat_id), style=PRIMARY),
            btn("🔁 Change Join Mode", cb=cb("adm:mode", chat_id), style=PRIMARY),
        ),
        full(btn("◀ Back", cb="adm:list", style=PRIMARY)),
    )


def _confirm_del_kb(chat_id: int) -> InlineKeyboardMarkup:
    return make_kb(
        row2(
            btn("✅ Yes, delete", cb=cb("adm:dely", chat_id), style=DANGER),
            btn("❌ No", cb=cb("adm:deln", chat_id), style=SUCCESS),
        )
    )


def _private_choice_kb(chat_id: int) -> InlineKeyboardMarkup:
    return make_kb(
        row2(
            btn("📩 Request wala", cb=cb("adm:ms", chat_id, "request"), style=PRIMARY),
            btn("🔗 Direct join", cb=cb("adm:ms", chat_id, "direct_private"), style=PRIMARY),
        ),
        full(btn("◀ Back", cb="back:admin", style=PRIMARY)),
    )


def _mode_choice_kb(chat_id: int) -> InlineKeyboardMarkup:
    return make_kb(
        row2(
            btn("📩 Request", cb=cb("adm:ms", chat_id, "request"), style=PRIMARY),
            btn("🔗 Direct", cb=cb("adm:ms", chat_id, "direct_private"), style=PRIMARY),
        ),
        full(btn("◀ Back", cb=cb("adm:ch", chat_id), style=PRIMARY)),
    )


# --------------------------------------------------------------- helpers ----

# Callback answers are capped at 200 chars by Telegram (M9) — long channel
# titles must never exceed that, or the whole answer is rejected.
_ALERT_CAP = 190


def _alert(text: str) -> str:
    """Truncate a callback-alert text to Telegram's ~200-char cap."""
    return text[:_ALERT_CAP]


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _title_of(chat) -> str:
    return chat.title or chat.username or str(chat.id)


async def _detail_text(bot, conn, chat_id: int) -> str | None:
    """Render the detail card; ``None`` when the channel isn't saved."""
    ch = await get_channel(conn, chat_id)
    if ch is None:
        return None
    try:
        members = str(await bot.get_chat_member_count(chat_id))
    except Exception:  # noqa: BLE001 - count is best-effort
        members = "—"
    try:
        admins = await bot.get_chat_administrators(chat_id)
        bot_admin = "✅ yes" if bot.id in {m.user.id for m in admins if m.user} else "❌ no"
    except Exception:  # noqa: BLE001
        bot_admin = "—"
    return texts.ADMIN_CHANNEL_DETAIL(
        title=ch.get("title") or ch.get("username") or chat_id,
        chat_id=chat_id,
        type_=ch.get("type") or "—",
        join_mode=ch.get("join_mode") or "—",
        invite=ch.get("invite_url") or "—",
        members=members,
        bot_admin=bot_admin,
        created_by=ch.get("created_by") or "—",
        created_at=ch.get("created_at") or "—",
    )


async def _show_detail(callback: CallbackQuery, bot, conn, chat_id: int) -> None:
    text = await _detail_text(bot, conn, chat_id)
    if text is None:
        await edit_or_send(callback, texts.ADMIN_NOT_FOUND, _back_admin_kb())
        return
    await edit_or_send(callback, text, _detail_kb(chat_id))


async def _show_list(callback: Callback, conn) -> None:
    channels = await list_channels(conn)
    if not channels:
        await edit_or_send(callback, texts.ADMIN_LIST_EMPTY, _channel_list_kb([]))
        return
    body = "\n".join(
        f"{i}. <b>{_esc(ch.get('title') or ch.get('username') or ch['chat_id'])}</b> "
        f"— <i>{_esc(ch.get('join_mode'))}</i>"
        for i, ch in enumerate(channels, start=1)
    )
    text = (
        "📋 <b>Channels</b>\n"
        "<blockquote>Tap one to inspect, change its join mode or delete it.</blockquote>"
        f"\n\n{body}"
    )
    await edit_or_send(callback, text, _channel_list_kb(channels))


async def _bot_is_admin(bot, chat_id: int) -> bool:
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError:  # M2: base class covers bad request/forbidden/etc.
        return False
    return bot.id in {m.user.id for m in admins if m.user}


async def _save_channel(
    conn, bot, chat, *, join_mode: str, invite_url: str | None, created_by: int
) -> None:
    await add_channel(
        conn,
        chat_id=chat.id,
        username=getattr(chat, "username", None),
        title=chat.title,
        type=chat.type,
        join_mode=join_mode,
        invite_url=invite_url,
        created_by=created_by,
    )


# ------------------------------------------------------------- handlers -----

@router.callback_query(F.data == "adm:menu")
async def cb_admin_menu(callback: CallbackQuery) -> None:
    await edit_or_send(callback, texts.ADMIN_MENU, _admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "back:admin")
async def cb_back_admin(callback: CallbackQuery) -> None:
    await edit_or_send(callback, texts.ADMIN_MENU, _admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "adm:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddChannel.waiting_for_target)
    await edit_or_send(callback, texts.ADMIN_ADD_PROMPT, _add_prompt_kb())
    await callback.answer()


@router.message(AddChannel.waiting_for_target)
async def add_channel_target(message: Message, state: FSMContext, bot, db) -> None:
    """Accept a link, a @username or a forwarded post (PLAN R2)."""
    ref = None
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        # MessageOriginChannel exposes .chat; MessageOriginChat exposes
        # .sender_chat; hidden/user-only origins cannot resolve a chat.
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        if chat is None:
            await message.answer(texts.ADMIN_FORWARD_HIDDEN)
            return
        ref = chat.id
    elif getattr(message, "forward_from_chat", None) is not None:  # legacy payloads
        ref = message.forward_from_chat.id
    elif message.text:
        parsed = parse_channel_target(message.text)
        if parsed is None:
            await message.answer(texts.ADMIN_INVALID_TARGET)
            return
        ref = parsed["ref"]
    else:
        await message.answer(texts.ADMIN_ADD_PROMPT)
        return

    try:
        chat = await bot.get_chat(ref)
    except (TelegramBadRequest, TelegramForbiddenError):
        await message.answer(texts.ADMIN_TARGET_UNRESOLVED)
        return

    if not await _bot_is_admin(bot, chat.id):
        await state.clear()
        await message.answer(
            texts.ADMIN_BOT_NOT_ADMIN.format(title=_esc(_title_of(chat))),
            reply_markup=_back_admin_kb(),
        )
        return

    if chat.username:  # public → direct join
        await _save_channel(
            db,
            bot,
            chat,
            join_mode="direct",
            invite_url=f"https://t.me/{chat.username}",
            created_by=message.from_user.id,
        )
        await state.clear()
        await message.answer(
            texts.ADMIN_SAVED.format(title=_esc(_title_of(chat)), mode="direct"),
            reply_markup=_saved_kb(),
        )
        return

    # private → user picks the join mode
    await state.set_state(AddChannel.waiting_for_choice)
    await state.update_data(
        chat_id=chat.id,
        title=chat.title,
        chat_type=chat.type,
        created_by=message.from_user.id,
    )
    await message.answer(
        texts.ADMIN_PRIVATE_CHOICE.format(title=_esc(_title_of(chat))),
        reply_markup=_private_choice_kb(chat.id),
    )


@router.callback_query(F.data == "adm:list")
async def cb_admin_list(callback: CallbackQuery, db) -> None:
    await _show_list(callback, db)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ch:"))
async def cb_admin_detail(callback: CallbackQuery, bot, db) -> None:
    chat_id = int(callback.data.split(":")[2])
    await _show_detail(callback, bot, db, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:del:"))
async def cb_admin_del(callback: CallbackQuery, db) -> None:
    chat_id = int(callback.data.split(":")[2])
    ch = await get_channel(db, chat_id)
    if ch is None:
        await edit_or_send(callback, texts.ADMIN_NOT_FOUND, _back_admin_kb())
        await callback.answer(_alert("Channel not found"), show_alert=True)
        return
    title = _esc(ch.get("title") or ch.get("username") or chat_id)
    await edit_or_send(
        callback,
        texts.ADMIN_DELETE_CONFIRM.format(title=title),
        _confirm_del_kb(chat_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:dely:"))
async def cb_admin_del_yes(callback: CallbackQuery, db) -> None:
    chat_id = int(callback.data.split(":")[2])
    ch = await get_channel(db, chat_id)
    raw_title = str((ch or {}).get("title") or (ch or {}).get("username") or chat_id)
    await delete_channel(db, chat_id)  # also drops cached verifications
    await _show_list(callback, db)
    # L3: alerts are plain text — use the no-HTML variant + M9 truncation.
    await callback.answer(
        _alert(texts.ADMIN_DELETED_ALERT.format(title=raw_title)), show_alert=True
    )


@router.callback_query(F.data.startswith("adm:deln:"))
async def cb_admin_del_no(callback: CallbackQuery, bot, db) -> None:
    chat_id = int(callback.data.split(":")[2])
    await _show_detail(callback, bot, db, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:mode:"))
async def cb_admin_mode(callback: CallbackQuery, db) -> None:
    chat_id = int(callback.data.split(":")[2])
    ch = await get_channel(db, chat_id)
    if ch is None:
        await edit_or_send(callback, texts.ADMIN_NOT_FOUND, _back_admin_kb())
        await callback.answer(_alert("Channel not found"), show_alert=True)
        return
    if ch.get("username"):  # public → always direct
        await callback.answer(
            _alert("Public channel — direct join only"), show_alert=True
        )
        return
    title = _esc(ch.get("title") or ch.get("username") or chat_id)
    await edit_or_send(
        callback,
        texts.ADMIN_PRIVATE_CHOICE.format(title=title),
        _mode_choice_kb(chat_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ms:"))
async def cb_admin_mode_set(callback: CallbackQuery, state: FSMContext, bot, db) -> None:
    """Set the join mode; regenerate the invite link for private chats."""
    _, _, chat_id_s, mode = callback.data.split(":", 3)
    chat_id = int(chat_id_s)
    if mode not in ("request", "direct_private"):
        await callback.answer(_alert("Unknown mode"), show_alert=True)
        return

    ch = await get_channel(db, chat_id)
    raw_title = str((ch or {}).get("title") or (ch or {}).get("username") or chat_id)
    try:
        chat = await bot.get_chat(chat_id)
        raw_title = _title_of(chat)
    except (TelegramBadRequest, TelegramForbiddenError):
        chat = None
    if ch is None and chat is None:
        await edit_or_send(callback, texts.ADMIN_NOT_FOUND, _back_admin_kb())
        await callback.answer(_alert("Channel not found"), show_alert=True)
        return

    if mode == "request":
        try:
            link = await bot.create_chat_invite_link(
                chat_id, name=f"OSINT {raw_title}"[:32], creates_join_request=True
            )
        except (TelegramBadRequest, TelegramForbiddenError):
            await callback.answer(
                _alert("⚠️ Can't create a request invite — check bot admin rights"),
                show_alert=True,
            )
            return
        invite = link.invite_link
    else:
        try:
            invite = await bot.export_chat_invite_link(chat_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            await callback.answer(
                _alert("⚠️ Missing right: invite users via link"), show_alert=True
            )
            return

    if ch is None:
        await _save_channel(
            db,
            bot,
            chat,
            join_mode=mode,
            invite_url=invite,
            created_by=callback.from_user.id,
        )
    else:
        await set_join_mode(db, chat_id, mode)
        await set_invite_url(db, chat_id, invite)
    await state.clear()

    await _show_detail(callback, bot, db, chat_id)
    # L3: plain-text (no-HTML) alert variant + M9 truncation at source.
    # Callback answers are capped at 200 chars by Telegram.
    await callback.answer(
        _alert(
            texts.ADMIN_MODE_SET_ALERT.format(
                title=raw_title, mode=mode, invite=invite
            )
        ),
        show_alert=False,
    )
