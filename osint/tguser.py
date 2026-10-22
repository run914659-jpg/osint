"""Telegram @username / t.me link → public chat info (needs the aiogram bot).

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

``extract_username()`` is a pure normalization helper (unit-tested offline);
all Telegram calls happen in ``lookup()`` with an 8 s budget.
"""

from __future__ import annotations

import asyncio
import re
from html import escape
from urllib.parse import urlparse

from osint import ToolError

__all__ = ["lookup", "render", "extract_username"]

_TELEGRAM_HOSTS = {
    "t.me", "www.t.me", "telegram.me", "www.telegram.me",
    "telegram.dog", "www.telegram.dog", "telesco.pe",
}
_LINK_PREFIXES = (
    "t.me/", "www.t.me/", "telegram.me/", "www.telegram.me/",
    "telegram.dog/", "www.telegram.dog/",
)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

_INVITE = "invite links are unsupported — send a public @username"

_TYPE_ICONS = {
    "user": "👤 User",
    "private": "🔒 Private",
    "channel": "📢 Channel",
    "group": "👥 Group",
    "supergroup": "👥 Supergroup",
    "sender": "👤 Sender",
    "unknown": "❓ Unknown",
}


def extract_username(raw: str) -> str:
    """Normalize ``@user`` / ``t.me/user`` / ``https://t.me/user`` / bare → slug.

    Raises ToolError for invite links and non-Telegram URLs; does NOT enforce
    the 5–32 char username rule (that happens in :func:`lookup`).
    """
    s = (raw or "").strip()
    if not s:
        raise ToolError("send a @username or t.me link (e.g. @telegram)")

    low = s.lower()
    if "joinchat" in low:
        raise ToolError(_INVITE)

    if low.startswith(("http://", "https://")):
        parsed = urlparse(s)
        host = (parsed.hostname or "").lower()
        if host not in _TELEGRAM_HOSTS:
            raise ToolError("send a @username or t.me link (e.g. @telegram)")
        path = parsed.path
    else:
        path = s
        for prefix in _LINK_PREFIXES:
            if low.startswith(prefix):
                path = s[len(prefix):]
                break

    parts = [p for p in path.lstrip("/").split("/") if p != ""]
    if not parts:
        raise ToolError("send a @username or t.me link (e.g. @telegram)")

    slug = parts[0].split("?")[0].split("#")[0]
    if slug.startswith("+"):
        raise ToolError(_INVITE)
    if slug.lower() == "c":
        raise ToolError("private channel links are unsupported — "
                        "send the public @username")
    if slug.lower() == "s":  # t.me/s/channel preview page
        parts = parts[1:]
        if not parts:
            raise ToolError("send a @username or t.me link (e.g. @telegram)")
        slug = parts[0].split("?")[0].split("#")[0]

    slug = slug.lstrip("@")
    if not slug:
        raise ToolError("send a @username or t.me link (e.g. @telegram)")
    return slug


def _validate_username(slug: str) -> str:
    if not _USERNAME_RE.match(slug):
        raise ToolError(
            "invalid username — 5–32 characters, letters, digits and "
            "underscores only"
        )
    return slug


def _as_dict(obj) -> dict:
    """aiogram/pydantic model → plain JSON dict (defensive)."""
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    return {}


async def lookup(q: str, cfg, bot=None) -> dict:
    username = _validate_username(extract_username(q))
    if bot is None:
        raise ToolError("Telegram is unavailable right now — try again shortly")

    try:
        chat = await asyncio.wait_for(bot.get_chat("@" + username), 8)
    except Exception:
        raise ToolError(
            "user/channel not found or privacy-restricted"
        ) from None

    info = _as_dict(chat)
    chat_id = info.get("id", getattr(chat, "id", None))
    if chat_id is None:
        raise ToolError("user/channel not found or privacy-restricted")

    ctype = str(info.get("type") or "unknown")
    data: dict = {
        "username": username,
        "id": int(chat_id),
        "type": ctype,
        "title": info.get("title") or "",
        "first_name": info.get("first_name") or "",
        "last_name": info.get("last_name") or "",
        "description": info.get("description") or info.get("bio") or "",
        "members": None,
    }

    if ctype in ("channel", "group", "supergroup"):
        try:
            count = await asyncio.wait_for(
                bot.get_chat_member_count(chat_id), 8
            )
            data["members"] = int(count)
        except Exception:
            data["members"] = None

    if username:
        data["buttons"] = [
            (f"↗ Open @{username}", f"https://t.me/{username}")
        ]
    return data


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    username = str(d.get("username") or "")
    ctype = str(d.get("type") or "unknown")
    type_label = _TYPE_ICONS.get(ctype, f"❓ {ctype.title()}")
    chat_id = d.get("id")

    title = str(d.get("title") or "").strip()
    if not title:
        name = " ".join(
            p for p in (str(d.get("first_name") or ""),
                        str(d.get("last_name") or "")) if p
        ).strip()
        title = name or (f"@{username}" if username else "unknown")

    head = f"@{username}" if username else (str(chat_id) if chat_id else "?")
    members = d.get("members")
    member_bit = (
        f" · 👥 {int(members):,}" if isinstance(members, int) else ""
    )
    lines = [
        f"<blockquote>🔍 {escape(head)} — {type_label}{member_bit}</blockquote>",
        "",
    ]

    # Title doubles as a tg:// mention link for plain users.
    safe_title = escape(title)
    if ctype in ("user", "private") and chat_id:
        name_html = f'<a href="tg://user?id={int(chat_id)}">{safe_title}</a>'
    else:
        name_html = safe_title
    lines.append(f"<b>Name:</b> {name_html}")

    if username:
        lines.append(f"<b>Username:</b> <code>@{escape(username)}</code>")
    lines.append(f"<b>Type:</b> {type_label}")
    if chat_id is not None:
        lines.append(f"<b>ID:</b> <code>{escape(str(chat_id))}</code>")

    bio = str(d.get("description") or "").strip()
    if bio:
        lines.append(f"<b>Bio:</b> {escape(bio)}")

    if isinstance(members, int):
        label = "Subscribers" if ctype == "channel" else "Members"
        lines.append(f"<b>{label}:</b> 👥 <code>{members:,}</code>")
    elif ctype in ("channel", "group", "supergroup"):
        lines.append(
            "<b>Members:</b> ❓ unavailable (bot lacks access to the chat)"
        )

    return "\n".join(lines)
