"""Layout rules D1–D6 — the Phase 2 gate (PLAN.md §5)."""

from pathlib import Path

import pytest
from aiogram.types import InlineKeyboardButton

from osintbot.keyboards import (
    DANGER,
    MAX_CB_BYTES,
    PRIMARY,
    SUCCESS,
    btn,
    cb,
    chain,
    full,
    make_kb,
    menu,
    row2,
)

ALLOWED_STYLES = {None, PRIMARY, SUCCESS, DANGER}

ROOT = Path(__file__).resolve().parents[1]


def _items(n: int) -> list[InlineKeyboardButton]:
    return [btn(f"Item {i}", cb=cb("t", "num", i)) for i in range(n)]


def _all_buttons(rows: list[list[InlineKeyboardButton]]):
    for row in rows:
        for b in row:
            yield b


def _sample_menu(n: int = 6) -> list[list[InlineKeyboardButton]]:
    return menu(_items(n), back_cb="back:menu")


def _sample_chain() -> list[list[InlineKeyboardButton]]:
    return chain(
        row2(btn("🔍 Phone", cb="t:num"), btn("✉️ Email", cb="t:mail")),
        row2(btn("👤 User", cb="t:user"), btn("🌐 IP", cb="t:ip")),
        tail=btn("◀ Back", cb="back:menu", style=PRIMARY),
    )


# (a) back/verify tail is the LAST row and that row has EXACTLY 1 button -----

@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8])
def test_menu_back_is_last_row_alone(n):
    rows = _sample_menu(n)
    assert len(rows[-1]) == 1, "back row must contain exactly one button"
    assert rows[-1][0].callback_data == "back:menu"


def test_chain_tail_is_last_row_alone():
    rows = _sample_chain()
    assert len(rows[-1]) == 1, "tail row must contain exactly one button"
    assert rows[-1][0].callback_data == "back:menu"


def test_chain_tail_cannot_be_buried_in_a_2up_row():
    # tail is appended after every caller row, always standalone
    rows = chain(
        row2(btn("A", cb="t:num"), btn("B", cb="t:mail")),
        tail=btn("✔ Verify", cb="vj:check", style=SUCCESS),
    )
    assert rows[-1] == [rows[-1][0]]
    assert rows[-1][0].callback_data == "vj:check"
    assert len(rows) == 2  # one caller row + standalone tail


# (b) every non-tail row has exactly 2 buttons -------------------------------

@pytest.mark.parametrize("n", [2, 4, 6, 8])
def test_menu_non_tail_rows_are_2up(n):
    rows = _sample_menu(n)
    for row in rows[:-1]:
        assert len(row) == 2, f"non-tail row must have 2 buttons, got {len(row)}"


def test_chain_non_tail_rows_are_2up():
    rows = _sample_chain()
    for row in rows[:-1]:
        assert len(row) == 2, f"non-tail row must have 2 buttons, got {len(row)}"


def test_row2_and_full_enforce_shapes():
    a, b = btn("A", cb="t:num"), btn("B", cb="t:mail")
    assert row2(a, b) == [a, b]
    with pytest.raises(TypeError):
        row2(a)  # wrong arity fails loudly
    assert len(full(a, b, btn("C", cb="t:ip"))) == 3
    with pytest.raises(AssertionError):
        full()


# (c) styles restricted to {None, primary, success, danger} ------------------

def test_styles_are_restricted_to_allowed_set():
    rows = menu(
        [
            btn("Direct", cb="t:num", style=SUCCESS),
            btn("Request", cb="t:mail", style=PRIMARY),
            btn("Delete", cb="adm:dely:-100", style=DANGER),
            btn("Plain", cb="t:ip"),
        ],
        back_cb="back:menu",
    )
    for button in _all_buttons(rows):
        assert getattr(button, "style", None) in ALLOWED_STYLES


def test_invalid_style_rejected():
    with pytest.raises(AssertionError):
        btn("X", cb="t:num", style="rainbow")


def test_make_kb_wraps_rows():
    kb = make_kb(*_sample_menu(4))
    # 4 items → two 2-up rows + the tail row
    assert len(kb.inline_keyboard) == 3
    assert len(kb.inline_keyboard[-1]) == 1


# (d) no ReplyKeyboard anywhere in the package -------------------------------

def test_no_reply_keyboard_anywhere():
    hits: list[str] = []
    scan_dirs = [ROOT / "osintbot", ROOT / "handlers"]
    for d in scan_dirs:
        if not d.exists():
            continue
        for path in list(d.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ReplyKeyboard" in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == [], f"ReplyKeyboard* found in: {hits}"


# (e) callback_data ≤ 64 bytes on every constructed button -------------------

def test_all_constructed_callbacks_fit_64_bytes():
    big_chat = -1001234567890123
    rows = menu(
        [
            btn("Detail", cb=cb("adm:ch", big_chat)),
            btn("Delete", cb=cb("adm:del", big_chat)),
            btn("Mode", cb=cb("adm:mode", big_chat)),
            btn("Set", cb=cb("adm:ms", big_chat, "direct_private")),
        ],
        back_cb="back:admin",
    )
    for button in _all_buttons(rows):
        if button.callback_data:
            assert len(button.callback_data.encode("utf-8")) <= MAX_CB_BYTES


def test_btn_rejects_oversized_callback():
    too_long = "x" * (MAX_CB_BYTES + 1)
    with pytest.raises(AssertionError):
        btn("Bad", cb=too_long)


def test_btn_requires_exactly_one_of_cb_url():
    with pytest.raises(AssertionError):
        btn("Neither")
    with pytest.raises(AssertionError):
        btn("Both", cb="t:num", url="https://example.com")
    # disabled → inert noop callback, never a url button
    inert = btn("Later", url="https://example.com", disabled=True)
    assert inert.callback_data == "noop"


def test_url_button_and_disabled_button_build():
    link = btn("Join", url="https://t.me/+invite", style=PRIMARY)
    assert link.url == "https://t.me/+invite"
    assert getattr(link, "style", None) == PRIMARY
