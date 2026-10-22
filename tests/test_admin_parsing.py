"""Unit tests for the admin target parser + verify helpers (PLAN R2 / Phase 4).

Everything here is pure — no network, no DB, no aiogram session.
"""

from __future__ import annotations

from types import SimpleNamespace

from aiogram.types import (
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    User,
)

from osintbot.handlers.admin import AddChannel, parse_channel_target
from osintbot.handlers.start import build_force_join_kb, build_menu_kb, is_joined
from osintbot.keyboards import MAX_CB_BYTES

_USER = User(id=1, is_bot=False, first_name="Test")

# ------------------------------------------------------------ parser: ok ----

ACCEPTED = [
    # (input, kind, ref)
    ("@mychannel", "username", "@mychannel"),
    ("  @mychannel  ", "username", "@mychannel"),
    ("t.me/mychannel", "username", "@mychannel"),
    ("https://t.me/mychannel", "username", "@mychannel"),
    ("http://t.me/mychannel", "username", "@mychannel"),
    ("HTTPS://T.ME/mychannel", "username", "@mychannel"),
    ("www.t.me/mychannel", "username", "@mychannel"),
    ("t.me/mychannel/123", "username", "@mychannel"),  # post link
    ("telegram.me/mychannel", "username", "@mychannel"),
    ("mychannel", "username", "@mychannel"),  # raw username
    ("my_channel_99", "username", "@my_channel_99"),
    # invite links
    ("t.me/+AbCdEfGh123", "invite", "https://t.me/+AbCdEfGh123"),
    ("https://t.me/+AbCdEfGh123", "invite", "https://t.me/+AbCdEfGh123"),
    ("+AbCdEfGh123", "invite", "https://t.me/+AbCdEfGh123"),
    ("~AbCdEfGh123", "invite", "https://t.me/+AbCdEfGh123"),
    ("t.me/joinchat/AAAAAE", "invite", "https://t.me/joinchat/AAAAAE"),
    ("https://t.me/joinchat/AAAAAE", "invite", "https://t.me/joinchat/AAAAAE"),
    ("t.me/+AbCdEf?single", "invite", "https://t.me/+AbCdEf"),
]


def test_accepted_targets():
    for raw, kind, ref in ACCEPTED:
        parsed = parse_channel_target(raw)
        assert parsed is not None, f"failed to parse {raw!r}"
        assert parsed["kind"] == kind, f"{raw!r}: kind {parsed['kind']} != {kind}"
        assert parsed["ref"] == ref, f"{raw!r}: ref {parsed['ref']!r} != {ref!r}"


REJECTED = [
    None,
    "",
    "   ",
    "https://example.com",
    "t.me/",
    "t.me",
    "@",
    "@bad name",
    "bad name here",
    "@ab",  # too short for a Telegram username (<3)
    "@Chan nel",
    "t.me/+",
    "t.me/joinchat/",
    "mailto:foo@bar.com",
    "javascript:alert(1)",
]


def test_rejected_targets():
    for raw in REJECTED:
        assert parse_channel_target(raw) is None, f"should reject {raw!r}"


def test_parser_is_pure_and_repeatable():
    # same input → same output, no state
    assert parse_channel_target("@x_channel") == parse_channel_target("@x_channel")


# ------------------------------------------------------------ FSM states ----

def test_add_channel_states_exist():
    assert AddChannel.waiting_for_target.state == "AddChannel:waiting_for_target"
    assert AddChannel.waiting_for_choice.state == "AddChannel:waiting_for_choice"


# ------------------------------------------------------------- is_joined ----

def test_is_joined_statuses():
    assert is_joined(ChatMemberMember(status="member", user=_USER))
    assert is_joined(ChatMemberOwner(status="creator", user=_USER, is_anonymous=False))
    assert is_joined(
        SimpleNamespace(status="administrator", user=_USER)
    )
    # restricted: only counts when is_member is truthy
    assert is_joined(SimpleNamespace(status="restricted", is_member=True))
    assert not is_joined(SimpleNamespace(status="restricted", is_member=False))
    assert not is_joined(ChatMemberLeft(status="left", user=_USER))
    assert not is_joined(
        ChatMemberBanned(status="kicked", user=_USER, until_date=0)
    )


# ----------------------------------------------------- keyboard invariants ---

def _channels(n: int) -> list[dict]:
    return [
        {
            "chat_id": -100_0000_0000 + i,
            "username": f"chan{i}",
            "title": f"Channel {i}",
            "type": "channel",
            "join_mode": "request" if i % 2 else "direct",
            "invite_url": f"https://t.me/chan{i}",
        }
        for i in range(n)
    ]


def test_menu_kb_layout_rules():
    kb = build_menu_kb(is_admin=True)
    rows = kb.inline_keyboard
    # root menu has no Back, no Verify, no channel buttons (L8)
    cbs = {b.callback_data for r in rows for b in r}
    assert "back:menu" not in cbs
    assert "vj:check" not in cbs
    assert not any(b.url for r in rows for b in r)
    # admin row is last, full width
    assert len(rows[-1]) == 1
    assert rows[-1][0].callback_data == "adm:menu"
    # all 16 tools present, 2-up
    tool_cbs = {cb for cb in cbs if cb and cb.startswith("t:")}
    assert len(tool_cbs) == 16
    # every callback fits Telegram's limit; feature rows ≤ 2 buttons
    for r in rows:
        assert 1 <= len(r) <= 2
        for b in r:
            if b.callback_data:
                assert len(b.callback_data.encode()) <= MAX_CB_BYTES


def test_menu_kb_no_admin_button_for_non_admin():
    kb = build_menu_kb(is_admin=False)
    cbs = {b.callback_data for r in kb.inline_keyboard for b in r}
    assert "adm:menu" not in cbs
    assert "vj:check" not in cbs  # join/verify only on the force-join gate
    assert "back:menu" not in cbs  # root menu has nowhere to go back to
    # ends on a plain 2-up tool row
    assert len(kb.inline_keyboard[-1]) == 2


def test_force_join_kb_tail_is_verify():
    kb = build_force_join_kb(_channels(2))
    rows = kb.inline_keyboard
    assert len(rows[-1]) == 1
    assert rows[-1][0].callback_data == "vj:check"
    # channel rows are url buttons
    assert rows[0][0].url == "https://t.me/chan0"
