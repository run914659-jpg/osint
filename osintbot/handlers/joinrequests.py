"""chat_join_request auto-approve (PLAN Phase 5 / R6).

When someone taps "Join" on a request-mode channel, Telegram sends a
``chat_join_request`` update. We approve it immediately (query-capability
check first, plain ``approveChatJoinRequest`` fallback), cache the verified
membership and DM a welcome with the Main Menu button.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatJoinRequest

from osintbot import texts
from osintbot.db import set_verified
from osintbot.keyboards import PRIMARY, btn, full, make_kb

__all__ = ["router"]

router = Router(name="joinrequests")


@router.chat_join_request()
async def on_chat_join_request(request: ChatJoinRequest, bot, db) -> None:
    chat_id = request.chat.id
    user_id = request.from_user.id

    # Capability check: aiogram builds without answerChatJoinRequestQuery
    # (or updates without query_id) fall back to approveChatJoinRequest.
    query_id = getattr(request, "query_id", None)
    try:
        if query_id and hasattr(bot, "answer_chat_join_request_query"):
            await bot.answer_chat_join_request_query(query_id, "approve")
        else:
            await bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        # Already approved, banned, or the bot lost rights — keep going so
        # the membership cache + welcome DM still land where possible.
        pass

    try:
        await set_verified(db, user_id, chat_id)
    except Exception:  # noqa: BLE001 - tracking must not break approval
        pass

    kb = make_kb(full(btn("➡ Main Menu", cb="back:menu", style=PRIMARY)))
    try:
        await bot.send_message(
            request.user_chat_id,
            texts.JOIN_REQUEST_APPROVED,
            reply_markup=kb,
            parse_mode="HTML",
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        # User never started the bot / blocked it — nothing else to do.
        pass
