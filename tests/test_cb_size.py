"""callback_data must stay ≤ 64 bytes (PLAN.md §3 callback scheme + §5)."""

import pytest

from osintbot.keyboards import MAX_CB_BYTES, btn, cb

# Worst-case realistic Telegram chat/channel ids
BIG_CHAT_IDS = [
    -1001234567890123,      # supergroup/channel style
    -1009999999999999,
    1234567890123456789,    # huge user id (max ~ 10^18, 19 digits)
]

# Every prefix from PLAN.md §3's callback_data table, with worst-case args
SAMPLE_PREFIXES = [
    ("adm:menu", ()),
    ("adm:add", ()),
    ("adm:list", ()),
    ("adm:ch", (-1001234567890123,)),
    ("adm:del", (-1001234567890123,)),
    ("adm:dely", (-1001234567890123,)),
    ("adm:deln", (-1001234567890123,)),
    ("adm:mode", (-1001234567890123,)),
    ("adm:ms", (-1001234567890123, "direct")),      # adm:mode:set short form
    ("adm:ms", (-1001234567890123, "direct_private")),
    ("back:menu", ()),
    ("back:admin", ()),
    ("back:tool", ()),
    ("vj:check", ()),
    ("ch:open", (-1001234567890123,)),
    ("t", ("num",)),
    ("t", ("mail",)),
    ("t", ("user",)),
    ("t", ("ip",)),
    ("t", ("url",)),
    ("t", ("tg",)),
    ("t", ("txt",)),
    ("t", ("rdap",)),
    ("noop", ()),
]


@pytest.mark.parametrize("prefix,args", SAMPLE_PREFIXES)
def test_plan_prefixes_fit(prefix, args):
    data = cb(prefix, *args)
    assert len(data.encode("utf-8")) <= MAX_CB_BYTES


@pytest.mark.parametrize("chat_id", BIG_CHAT_IDS)
def test_worst_case_chat_ids_fit(chat_id):
    for prefix, args in [
        ("adm:ch", (chat_id,)),
        ("adm:del", (chat_id,)),
        ("adm:dely", (chat_id,)),
        ("adm:deln", (chat_id,)),
        ("adm:mode", (chat_id,)),
        ("adm:ms", (chat_id, "direct_private")),
        ("ch:open", (chat_id,)),
    ]:
        assert len(cb(prefix, *args).encode("utf-8")) <= MAX_CB_BYTES


def test_cb_accepts_exact_64_bytes():
    # "t:" = 2 bytes, so 62 payload bytes == exactly 64
    data = cb("t", "x" * 62)
    assert len(data.encode("utf-8")) == MAX_CB_BYTES


def test_cb_rejects_oversized_args():
    with pytest.raises(AssertionError):
        cb("t", "x" * 63)  # 2 + 63 = 65 bytes > 64


def test_cb_rejects_oversized_multibyte_args():
    # UTF-8: rocket emoji = 4 bytes — must count bytes, not characters
    with pytest.raises(AssertionError):
        cb("adm:ch", "🚀" * 30)  # "adm:ch:" (7) + 120 bytes


def test_cb_counts_bytes_not_chars_for_bmp_chars():
    # 1-byte math would wrongly allow this; 2-byte UTF-8 chars matter
    with pytest.raises(AssertionError):
        cb("vj:check", "é" * 40)  # 9 + 80 = 89 bytes


def test_btn_enforces_64_byte_callback():
    with pytest.raises(AssertionError):
        btn("Go", cb="y" * 65)
    # boundary: exactly 64 bytes is fine
    ok = btn("Go", cb="y" * 64)
    assert len(ok.callback_data.encode("utf-8")) == MAX_CB_BYTES
