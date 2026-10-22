"""All HTML message templates (PLAN.md Phase 2).

Plain strings where no parameters are needed; small functions where they
are. Everything is rich HTML for Telegram's ``parse_mode="HTML"``:
emoji + ``<b>``/``<i>``/``<blockquote>``/``<pre>``/spoilers.
"""

from __future__ import annotations

import html as _html

__all__ = [
    "WELCOME",
    "ADMIN_MENU",
    "ADMIN_ADD_PROMPT",
    "ADMIN_NOT_ADMIN",
    "ADMIN_PRIVATE_CHOICE",
    "ADMIN_SAVED",
    "ADMIN_LIST_EMPTY",
    "ADMIN_DELETE_CONFIRM",
    "ADMIN_DELETED",
    "FORCE_JOIN",
    "FORCE_JOIN_OK",
    "JOIN_REQUEST_APPROVED",
    "MAIN_MENU",
    "ADMIN_DETAIL",
    "ADMIN_CHANNEL_DETAIL",
    "ADMIN_INVALID_TARGET",
    "ADMIN_FORWARD_HIDDEN",
    "ADMIN_TARGET_UNRESOLVED",
    "ADMIN_BOT_NOT_ADMIN",
    "ADMIN_NOT_FOUND",
    "ADMIN_MODE_SET",
    "BOT_ONLINE",
    "TOOL_PROMPT",
    "TOOL_RESULT",
    "TOOL_ERR",
    "TOOL_TIMEOUT",
]


def _esc(text: object) -> str:
    """Escape dynamic values for Telegram HTML."""
    return _html.escape(str(text), quote=False)


# ------------------------------------------------------------- welcome -----

WELCOME = (
    "<pre>"
    "  ____ ___ ____   ___  ____  _____ _   _ _______   __\n"
    " / ___/ _ \\_  _|  \\ \\/ /\\ \\ / /_  _| \\ | | ____\\ \\ / /\n"
    " \\__ \\ | | || |    \\  /  \\ V /  | | |  \\| |  _|  \\ V / \n"
    " ___) |_| || |    /  \\   | |   | | | |\\  | |___  | |  \n"
    " |____/\\___/|_|   /_/\\_\\  |_|   |_| |_| \\_|_____| |_|  \n"
    "</pre>"
    "\n"
    "<blockquote>🕵️ <b>The premium OSINT toolkit</b> — 8 instant tools, "
    "zero noise, all inside Telegram.</blockquote>"
    "\n\n"
    "🔎 <b>What you can do here</b>\n"
    "• 📱 phone intel  • ✉️ email checks  • 👤 username sweep\n"
    "• 🌐 IP lookup  • 🌍 DNS/WHOIS  • 💬 Telegram info\n"
    "• 🛠 text utils  • 📡 URL analysis"
    "\n\n"
    "📜 <b>Rules</b>\n"
    "<span class=\"tg-spoiler\">1. For research &amp; defensive use only — "
    "no harassment, no spam.</span>\n"
    "<span class=\"tg-spoiler\">2. Results come from public sources; "
    "treat them as leads, not proof.</span>\n"
    "<span class=\"tg-spoiler\">3. Mass scraping or resale of data is banned."
    "</span>"
    "\n\n"
    "👇 Pick a tool from the menu below."
)

# --------------------------------------------------------------- admin -----

ADMIN_MENU = (
    "🛠 <b>Admin Panel</b>\n"
    "<blockquote>Manage the channels users must join before using tools.</blockquote>"
    "\n\n"
    "➕ add a channel  •  📋 list &amp; edit  •  🗑 delete"
)

ADMIN_ADD_PROMPT = (
    "➕ <b>Add a channel</b>\n\n"
    "Send me <b>one</b> of these:\n"
    "• a link — <code>t.me/somewhere</code>\n"
    "• an invite link — <code>t.me/+AbCdEf</code>\n"
    "• a username — <code>@somewhere</code>\n"
    "• or a <i>forwarded post</i> from the channel"
    "\n\n"
    "⚠️ The bot must already be an <b>administrator</b> there with the right to "
    "<i>invite users via link</i>."
)

ADMIN_NOT_ADMIN = (
    "⛔ <b>Not for you</b>\n"
    "<blockquote>Admins only — this command is restricted to the bot's "
    "operators.</blockquote>"
)

ADMIN_PRIVATE_CHOICE = (
    "🔒 <b>Private channel detected</b>\n"
    "<blockquote>{title}</blockquote>\n\n"
    "How should users join it?\n"
    "• 📩 <b>Request join</b> — you approve each user (auto-approved here)\n"
    "• 🔗 <b>Direct join</b> — one permanent invite link"
)

ADMIN_SAVED = (
    "✅ <b>Channel saved</b>\n"
    "<blockquote>{title}</blockquote>\n\n"
    "Mode: <b>{mode}</b>\n"
    "Users will now be gated on this channel."
)

ADMIN_LIST_EMPTY = (
    "📭 <b>No channels yet</b>\n"
    "<blockquote>Nothing to enforce — add your first channel to start "
    "gating tools.</blockquote>"
)

ADMIN_DETAIL = (
    "📡 <b>{title}</b>\n\n"
    "🆔 <code>{chat_id}</code>\n"
    "📂 Type: <b>{type}</b>\n"
    "🚪 Join mode: <b>{join_mode}</b>\n"
    "🔗 Invite: {invite}\n"
    "➕ Added by: <code>{created_by}</code>\n"
    "📅 Since: <code>{created_at}</code>"
)

ADMIN_DELETE_CONFIRM = (
    "🗑 <b>Delete this channel?</b>\n"
    "<blockquote>{title}</blockquote>\n\n"
    "All cached verifications for it are dropped too. This cannot be undone."
)

ADMIN_DELETED = (
    "🗑 <b>Deleted</b> — <i>{title}</i> is no longer gating the bot."
)

# ----------------------------------------------------------- force-join ----

def FORCE_JOIN(pending: list[dict]) -> str:
    """Force-join gate screen with the list of channels still pending."""
    lines = []
    for i, ch in enumerate(pending, start=1):
        title = _esc(ch.get("title") or ch.get("username") or ch.get("chat_id"))
        mode = ch.get("join_mode") or "direct"
        icon = "📩" if mode == "request" else "🔗"
        lines.append(f"{i}. {icon} <b>{title}</b>")
    joined = "\n".join(lines)
    return (
        "🔒 <b>Join required</b>\n"
        "<blockquote>Membership check failed — join <b>all</b> channels below, "
        "then tap Verify.</blockquote>\n\n"
        f"⏳ <b>Pending ({len(pending)}):</b>\n{joined}"
        "\n\n"
        "✅ Already a member? Hit <b>Verify</b> — we re-check instantly."
    )


FORCE_JOIN_OK = (
    "✅ <b>Membership verified!</b>\n"
    "<blockquote>All channels joined — the full toolkit is unlocked.</blockquote>"
)

JOIN_REQUEST_APPROVED = (
    "🎉 <b>Request approved — welcome!</b>\n"
    "<blockquote>You're in. Time to dig.</blockquote>"
)

# ----------------------------------------------------------- main menu -----

MAIN_MENU = (
    "🗂 <b>Main Menu</b>\n"
    "<blockquote>Choose a tool — results arrive in seconds.</blockquote>"
    "\n\n"
    "8 tools available. Type your query when prompted."
)


# --------------------------------------------------------------- tools -----

def TOOL_PROMPT(title: str) -> str:
    return (
        f"🧩 <b>{_esc(title)}</b>\n"
        "<blockquote>Send the query now — I'll run the lookup and report back."
        "</blockquote>\n\n"
        "💡 <i>Tip: precise input → precise output. "
        "Timeout is ~8 seconds per run.</i>"
    )


def TOOL_RESULT(title: str, body: str) -> str:
    return (
        f"📊 <b>{_esc(title)} — result</b>\n"
        f"<blockquote>{body}</blockquote>"
        "\n\n"
        "<span class=\"tg-spoiler\">Data sourced from public endpoints; "
        "verify independently before acting.</span>"
    )


def TOOL_ERR(msg: str) -> str:
    return (
        "⚠️ <b>Lookup failed</b>\n"
        f"<blockquote>{_esc(msg)}</blockquote>\n\n"
        "<i>Check the input and try again.</i>"
    )


TOOL_TIMEOUT = (
    "⏱ <b>Timed out</b>\n"
    "<blockquote>The source didn't answer within 8 seconds — "
    "it may be slow or down.</blockquote>\n\n"
    "<i>Retry, or simplify the query.</i>"
)


# -------------------------------------------------- wave 3 additions -------
# (Phases 3–5: entry/admin/force-join. Existing templates above untouched.)


BOT_ONLINE = "🟢 <b>Bot online</b> — all systems go."


ADMIN_INVALID_TARGET = (
    "🤔 <b>Didn't catch that</b>\n"
    "<blockquote>That doesn't look like a channel reference.</blockquote>\n\n"
    "Send <b>one</b> of these:\n"
    "• <code>@username</code>\n"
    "• <code>t.me/username</code>\n"
    "• <code>t.me/+invitehash</code>\n"
    "• or a <i>forwarded post</i> from the channel"
)


ADMIN_FORWARD_HIDDEN = (
    "🙈 <b>Forward hides the source</b>\n"
    "<blockquote>This forward doesn't reveal which channel it came from "
    "(anonymous admins / hidden origin).</blockquote>\n\n"
    "Send a <code>@username</code> or an invite link instead."
)


ADMIN_TARGET_UNRESOLVED = (
    "❌ <b>Can't reach that chat</b>\n"
    "<blockquote>Telegram didn't resolve the reference — the username may be "
    "wrong, or the invite link only works for channels the bot is already in.</blockquote>\n\n"
    "Forward a message from the channel instead, or double-check the link."
)


ADMIN_BOT_NOT_ADMIN = (
    "⚠️ <b>Bot is not an admin there</b>\n"
    "<blockquote>{title}</blockquote>\n\n"
    "Pehle bot ko us channel/group ka <b>administrator</b> banao with the right to "
    "<i>Invite users via link</i> — then try again.\n\n"
    "🧩 <i>Channel Settings → Administrators → Add Admin → @your_bot → "
    "✅ Invite users via link</i>"
)


ADMIN_NOT_FOUND = (
    "❓ <b>Channel not found</b>\n"
    "<blockquote>It's no longer in the database — it may have been deleted.</blockquote>"
)


ADMIN_MODE_SET = (
    "✅ <b>Join mode updated</b>\n"
    "<blockquote>{title}</blockquote>\n\n"
    "Mode: <b>{mode}</b>\n"
    "Invite: <code>{invite}</code>"
)


def ADMIN_CHANNEL_DETAIL(
    title: str,
    chat_id: int,
    type_: str,
    join_mode: str,
    invite: str,
    members: str,
    bot_admin: str,
    created_by: str,
    created_at: str,
) -> str:
    """Rich channel detail card (admin panel). All dynamic parts escaped."""
    return (
        "📡 <b>{title}</b>\n\n"
        "🆔 <code>{chat_id}</code>\n"
        "📂 Type: <b>{type_}</b>\n"
        "👥 Members: <b>{members}</b>\n"
        "🚪 Join mode: <b>{join_mode}</b>\n"
        "🤖 Bot is admin: <b>{bot_admin}</b>\n"
        "🔗 Invite: <code>{invite}</code>\n"
        "➕ Added by: <code>{created_by}</code>\n"
        "📅 Since: <code>{created_at}</code>"
    ).format(
        title=_esc(title),
        chat_id=chat_id,
        type_=_esc(type_),
        members=_esc(members),
        join_mode=_esc(join_mode),
        bot_admin=_esc(bot_admin),
        invite=_esc(invite or "—"),
        created_by=_esc(created_by or "—"),
        created_at=_esc((created_at or "—")[:19]),
    )
