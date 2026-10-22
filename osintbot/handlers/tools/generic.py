"""ONE generic flow behind all 16 ``t:<code>`` tool buttons (WAVE3 / PLAN §6).

Flow: tap ``t:<code>`` → prompt card + FSM ``ToolFlow.waiting_query`` →
user text → loading animation → ``asyncio.wait_for(module.lookup(...))``
→ ``module.render(result)`` rich result, or a danger error card.

Keyboard/text builders are **pure functions** (no bot, no I/O) so
``tests/test_tools_flow.py`` can assert the layout rules directly:

* Back always owns its own last full-width row;
* feature rows are 2-up (an odd button stands alone);
* ``callback_data`` ≤ 64 bytes; styles ∈ {None, primary, success, danger}.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from osint import ToolError
from osintbot.handlers.start import edit_or_send
from osintbot.handlers.tools import TOOLS_REGISTRY  # package init builds it first
from osintbot.keyboards import (
    DANGER,
    HAS_COPY_TEXT,
    MAX_CB_BYTES,
    PRIMARY,
    btn,
    full,
    make_kb,
    row2,
)
from osintbot.loaders import LoadingAnimation

__all__ = [
    "ToolFlow",
    "LOOKUP_TIMEOUT",
    "register",
    "build_prompt_text",
    "build_prompt_kb",
    "build_result_kb",
    "build_error_text",
    "build_timeout_text",
    "build_error_kb",
]

logger = logging.getLogger("osintbot.tools")

# Safety net over the modules' own internal 8 s budgets.
LOOKUP_TIMEOUT = 11.0

SEARCHING = "🔍 <b>Searching</b>..."

# CopyTextButton.text Bot API limit (1-256 characters).
_MAX_COPY_CHARS = 256

# Fallback alert length for pre-copy_text aiogram builds (alert cap ≈ 200).
_ALERT_CHARS = 190


class ToolFlow(StatesGroup):
    """FSM for the generic tool query flow (PLAN §3)."""

    waiting_query = State()


# --------------------------------------------------------------- builders ----
# Pure functions: tests call these WITHOUT a bot.


def build_prompt_text(code: str) -> str:
    """Prompt card for ``t:<code>``: emoji + title + prompt (+ example)."""
    tool = TOOLS_REGISTRY[code].tool
    text = (
        f"{tool.emoji} <b>{html.escape(tool.title)}</b>\n"
        f"\n{html.escape(tool.prompt)}"
    )
    if tool.sample:
        text += f"\n<i>Example: <code>{html.escape(tool.sample)}</code></i>"
    return text


def build_prompt_kb() -> InlineKeyboardMarkup:
    """Prompt keyboard: Back owns the (only) full-width row."""
    return make_kb(full(btn("◀ Back", cb="back:menu", style=PRIMARY)))


def _chunk2(items: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    """Chunk buttons 2-up; a lone leftover becomes its own row."""
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(items), 2):
        chunk = items[i : i + 2]
        rows.append(row2(*chunk) if len(chunk) == 2 else full(*chunk))
    return rows


def _tool_button(entry) -> InlineKeyboardButton | None:
    """Module button ``(text, url_or_cb[, style])`` → button, or ``None``.

    Values starting with ``http`` (or any ``scheme://`` URL such as
    ``tg://user?id=…``) become URL buttons; everything else is a callback.
    Malformed / oversized entries are dropped with a warning — one bad
    button from a module must never break a finished lookup.
    """
    if not isinstance(entry, (tuple, list)) or len(entry) < 2:
        logger.warning("tool result: malformed button entry %r", entry)
        return None
    text, value = str(entry[0]), str(entry[1])
    style = entry[2] if len(entry) >= 3 else None
    try:
        if value.startswith("http") or "://" in value:
            return btn(text, url=value, style=style)
        return btn(text, cb=value, style=style)
    except AssertionError as exc:
        logger.warning("tool result: rejected button %r (%s)", entry, exc)
        return None


def _copy_button(query: str) -> InlineKeyboardButton:
    """📋 Copy button — copies the QUERY (for re-runs / editing).

    Preferred: a real clipboard ``copy_text`` button. On aiogram builds
    without ``CopyTextButton`` we fall back to a ``qcopy:`` callback that
    echoes the query in an alert (falls inert if it won't fit 64 bytes).
    """
    q = (query or "").strip()
    if not q:
        return btn("📋 Copy", cb="noop")  # nothing to copy (defensive)
    if HAS_COPY_TEXT:
        # Truncated to the Bot API's 256-char limit so a long query can
        # never make Telegram reject the whole result message.
        return btn("📋 Copy", copy_text=q[:_MAX_COPY_CHARS])
    data = f"qcopy:{q}"
    if len(data.encode("utf-8")) <= MAX_CB_BYTES:
        return btn("📋 Copy", cb=data)
    return btn("📋 Copy", cb="noop")


def build_result_kb(
    code: str, result: dict, partial: bool, *, query: str = ""
) -> InlineKeyboardMarkup:
    """Result keyboard: module buttons → Again+Copy → [partial note] → Back.

    Back is strictly the last row and strictly alone; the ⏱ partial note
    (DANGER, inert ``noop``) sits directly above it when the loader hit its
    10 s cap.
    """
    rows: list[list[InlineKeyboardButton]] = []

    entries = result.get("buttons", []) if isinstance(result, dict) else []
    items = [b for b in (_tool_button(e) for e in (entries or [])) if b is not None]
    rows.extend(_chunk2(items))

    q = query or (str(result.get("query") or "") if isinstance(result, dict) else "")
    rows.append(row2(btn("🔄 Again", cb=f"t:{code}"), _copy_button(q)))

    if partial:
        rows.append(full(btn("⏱ Data may be partial (slow source)", cb="noop", style=DANGER)))

    rows.append(full(btn("◀ Back to Menu", cb="back:menu", style=PRIMARY)))
    return make_kb(*rows)


def build_error_text(title: str, err: object) -> str:
    """Danger error card — escaped message + a concrete tip."""
    return (
        f"⚠️ <b>{html.escape(title)}</b>\n"
        f"\n<blockquote>{html.escape(str(err))}</blockquote>\n"
        "<i>Tip: check the input format and try again.</i>"
    )


def build_timeout_text(title: str) -> str:
    """Friendly timeout card (asyncio safety net fired)."""
    return (
        f"⏱ <b>{html.escape(title)}</b>\n"
        "\n<blockquote>Timed out — the source didn't answer within "
        f"{int(LOOKUP_TIMEOUT)} seconds. It may be slow or down.</blockquote>\n"
        "<i>Tip: retry, or simplify the query.</i>"
    )


def build_error_kb(code: str, title: str, err: object) -> InlineKeyboardMarkup:
    """Error keyboard: danger Try Again row + Back row (Back strictly last).

    ``title``/``err`` mirror :func:`build_error_text`'s signature so text
    and keyboard builders always travel as a pair.
    """
    _ = title, err
    return make_kb(
        full(btn("🔄 Try Again", cb=f"t:{code}", style=DANGER)),
        full(btn("◀ Back to Menu", cb="back:menu", style=PRIMARY)),
    )


# ---------------------------------------------------------------- helpers ----


async def _edit_or_new(bot, chat_id: int, message_id: int | None, text: str, kb) -> None:
    """Edit the loading/result message; fresh send when it's gone.

    ``message is not modified`` is swallowed silently (PLAN §6), a deleted
    message falls through to a new send, and a dead chat never raises out
    of the tool flow.
    """
    if message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
        except TelegramForbiddenError:
            pass
        # e.g. "message to edit not found" → send a fresh one below.
    try:
        await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    except Exception:  # noqa: BLE001 - user blocked us / stale chat
        logger.warning("tool reply failed in chat %s", chat_id, exc_info=True)


# --------------------------------------------------------------- handlers ----


async def cb_tool_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """``t:<code>`` → prompt card + arm ``ToolFlow.waiting_query``."""
    code = (callback.data or "")[2:]
    runtime = TOOLS_REGISTRY.get(code)
    if runtime is None:
        await callback.answer("Unknown tool", show_alert=True)
        return

    await callback.answer()  # clear the button spinner
    # State first: even if the Telegram edit below fails, the user can type.
    await state.set_state(ToolFlow.waiting_query)
    await state.update_data(code=code)
    await edit_or_send(callback, build_prompt_text(code), build_prompt_kb())


async def handle_tool_query(
    message: Message, state: FSMContext, bot, config
) -> None:
    """The user's query text → loader → lookup → result (or error card)."""
    data = await state.get_data()
    code = str(data.get("code") or "")
    await state.clear()  # EARLY: a failed lookup must not trap the user

    runtime = TOOLS_REGISTRY.get(code)
    title = runtime.tool.title if runtime is not None else "Tool"
    q = (message.text or "").strip()
    chat_id = message.chat.id

    loading_id: int | None = None
    loader: LoadingAnimation | None = None
    partial = False
    try:
        if runtime is None:
            raise ToolError("Unknown tool — pick one from the menu.")
        if not q:
            raise ToolError("empty query")

        sent = await bot.send_message(chat_id, SEARCHING, parse_mode="HTML")
        loading_id = sent.message_id
        loader = LoadingAnimation(bot, chat_id, loading_id, SEARCHING, query=q)
        await loader.start()
        try:
            result = await asyncio.wait_for(
                runtime.module.lookup(q, config, bot), LOOKUP_TIMEOUT
            )
        finally:
            partial = await loader.stop()  # always stop, even on error
            loader = None

        if not isinstance(result, dict):
            raise ToolError("The lookup returned an unexpected response.")
        try:
            body = runtime.module.render(result)
        except Exception as exc:  # noqa: BLE001 - render bug → error card
            logger.exception("render() failed for tool %s", code)
            raise ToolError(f"Could not format the result: {exc}") from exc
        if not isinstance(body, str):
            raise ToolError("render() returned non-text output.")

        kb = build_result_kb(code, result, partial, query=q)
        await _edit_or_new(bot, chat_id, loading_id, body, kb)
    except asyncio.TimeoutError:
        logger.warning("tool %s timed out after %.0fs", code, LOOKUP_TIMEOUT)
        await _edit_or_new(
            bot,
            chat_id,
            loading_id,
            build_timeout_text(title),
            build_error_kb(code, title, "timeout"),
        )
    except Exception as exc:  # noqa: BLE001 - ToolError/ToolTimeout/anything
        logger.warning("tool %s lookup failed: %s", code, exc, exc_info=True)
        await _edit_or_new(
            bot,
            chat_id,
            loading_id,
            build_error_text(title, exc),
            build_error_kb(code, title, str(exc)),
        )
    finally:
        if loader is not None:
            await loader.stop()


async def cb_noop(callback: CallbackQuery) -> None:
    """Inert button sink (disabled / partial-note buttons)."""
    await callback.answer()


async def cb_copy_query(callback: CallbackQuery) -> None:
    """Pre-copy_text fallback: echo the query in an alert (≤ 200 chars)."""
    query = (callback.data or "")[6:]  # strip "qcopy:"
    await callback.answer(query[:_ALERT_CHARS] or "Nothing to copy", show_alert=True)


def register(router: Router) -> None:
    """Attach every tool handler to ``router`` (called by ``handlers.tools``)."""
    router.callback_query.register(cb_tool_prompt, F.data.startswith("t:"))
    router.callback_query.register(cb_noop, F.data == "noop")
    router.callback_query.register(cb_copy_query, F.data.startswith("qcopy:"))
    router.message.register(
        handle_tool_query,
        ToolFlow.waiting_query,
        F.text,
        F.chat.type == "private",
    )
