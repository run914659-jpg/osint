"""Telegram numeric user ID → profile info (needs the aiogram bot).

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

HARD RULE: Telegram's Bot API never exposes phone numbers — this tool must
never output or imply one, and every render carries that footnote.
"""

from __future__ import annotations

import asyncio
import re
from html import escape

from osint import ToolError

__all__ = ["lookup", "render", "parse_user_id"]

_BAD_ID = "send a numeric ID, e.g. 777000"
_DIGITS_RE = re.compile(r"^[0-9]{1,15}$")

_PHONE_FOOTNOTE = (
    "ℹ️ Telegram never exposes phone numbers via Bot API — "
    "ID→number is impossible for any bot."
)


def parse_user_id(raw: str) -> int:
    """Normalize ``" 777000 "`` → ``777000``; raise ToolError otherwise."""
    s = (raw or "").strip()
    if not _DIGITS_RE.match(s):
        raise ToolError(_BAD_ID)
    value = int(s)
    if value <= 0:
        raise ToolError(_BAD_ID)
    return value


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
    uid = parse_user_id(q)
    if bot is None:
        raise ToolError("Telegram is unavailable right now — try again shortly")

    # Honest failure path: a user the bot has never interacted with cannot
    # be resolved via get_chat — that is NOT an error, it's the finding.
    try:
        obj = await asyncio.wait_for(bot.get_chat(uid), 8)
    except Exception:
        return {"found": False, "id": uid}

    info = _as_dict(obj)
    data: dict = {
        "found": True,
        "id": int(info.get("id") or uid),
        "username": info.get("username") or "",
        "first_name": info.get("first_name") or "",
        "last_name": info.get("last_name") or "",
        "language_code": info.get("language_code") or "",
        "is_premium": bool(info.get("is_premium")),
        "photos": None,
    }
    try:
        photos = await asyncio.wait_for(
            bot.get_user_profile_photos(uid, limit=1), 8
        )
        pinfo = _as_dict(photos)
        count = pinfo.get("total_count", getattr(photos, "total_count", None))
        data["photos"] = int(count) if count is not None else None
    except Exception:
        data["photos"] = None

    data["buttons"] = [("👤 Open profile", f"tg://user?id={data['id']}")]
    return data


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    uid = d.get("id")

    if not d.get("found"):
        lines = [
            f"<blockquote>🆔 {escape(str(uid) if uid else '?')} — "
            "❌ not visible to this bot</blockquote>",
            "",
            "❌ Never interacted with this bot / private — Telegram does not "
            "expose their profile to bots.",
            "",
            f"<span class=\"tg-spoiler\">{_PHONE_FOOTNOTE}</span>",
        ]
        return "\n".join(lines)

    name = " ".join(
        p for p in (str(d.get("first_name") or ""),
                    str(d.get("last_name") or "")) if p
    ).strip() or "Unknown"
    username = str(d.get("username") or "")

    lines = [
        f"<blockquote>🆔 {escape(str(uid))} — ✅ profile visible to this "
        "bot</blockquote>",
        "",
        f"<b>Name:</b> {escape(name)}",
    ]
    if username:
        lines.append(f"<b>Username:</b> <code>@{escape(username)}</code>")
    lines.append(f"<b>User ID:</b> <code>{escape(str(uid))}</code>")
    lines.append("<b>Type:</b> 👤 User")

    lang = str(d.get("language_code") or "")
    if lang:
        lines.append(f"<b>Language:</b> <code>{escape(lang)}</code>")

    lines.append(
        "<b>Premium:</b> ⭐ Yes" if d.get("is_premium")
        else "<b>Premium:</b> ➖ No"
    )

    photos = d.get("photos")
    if isinstance(photos, int):
        lines.append(
            f"<b>Profile photos:</b> 🖼️ <code>{photos}</code>"
            if photos
            else "<b>Profile photos:</b> 🖼️ none set"
        )

    lines.append("")
    lines.append(f"<span class=\"tg-spoiler\">{_PHONE_FOOTNOTE}</span>")
    return "\n".join(lines)
