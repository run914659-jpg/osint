"""WAVE3 — the unified tools flow: registry, prompt, keyboards, routes.

Everything here is offline: pure keyboard/text builders, the registry, the
router wiring, and stub-driven invocations of the two real handlers (no
network, no real Telegram — the bot is a recording stub).
"""

from __future__ import annotations

import dataclasses
import html
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User

from osint import ToolError
from osintbot import toolspec
from osintbot.config import Config
from osintbot.handlers import all_routers
from osintbot.handlers.tools import (
    TOOLS_REGISTRY,
    ToolRuntime,
    _load,
    tools_router,
)
from osintbot.handlers.tools import generic
from osintbot.handlers.tools.generic import (
    LOOKUP_TIMEOUT,
    ToolFlow,
    build_error_kb,
    build_error_text,
    build_prompt_kb,
    build_prompt_text,
    build_result_kb,
    build_timeout_text,
)
from osintbot.keyboards import (
    DANGER,
    HAS_COPY_TEXT,
    MAX_CB_BYTES,
    PRIMARY,
    SUCCESS,
    btn,
)

# The keyboards danger-lint no longer warns on Try Again / partial footers
# (L4 exempted them); the filter stays as belt-and-braces against any
# third-party UserWarning drowning the test summary.
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

ALLOWED_STYLES = {None, PRIMARY, SUCCESS, DANGER}


# ----------------------------------------------------------------- helpers --

def _rows(kb):
    return kb.inline_keyboard


def _assert_layout(rows, *, last_cb: str = "back:menu") -> None:
    """Layout rules: Back owns the last row alone, others ≤ 2, cb ≤ 64 B."""
    assert len(rows[-1]) == 1, "tail row must contain exactly one button"
    assert "Back" in rows[-1][0].text, "last row must be the Back button"
    assert rows[-1][0].callback_data == last_cb
    for row in rows[:-1]:
        assert len(row) <= 2, f"non-tail row must have 1..2 buttons, got {len(row)}"
    for row in rows:
        for b in row:
            if b.callback_data:
                assert len(b.callback_data.encode("utf-8")) <= MAX_CB_BYTES
            assert getattr(b, "style", None) in ALLOWED_STYLES


def _find_handler(observer, fn):
    return next(h for h in observer.handlers if h.callback is fn)


def _cb_event(data: str) -> CallbackQuery:
    return CallbackQuery(
        id="1",
        from_user=User(id=5, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        data=data,
    )


def _text_message(text: str, chat_id: int = 77) -> Message:
    return Message(
        message_id=1,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        text=text,
    )


def _fsm(chat_id: int = 77, user_id: int = 5) -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(chat_id=chat_id, user_id=user_id, bot_id=1),
    )


class _StubMsg:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


class _StubBot:
    """Records every send/edit — enough for loader + handler + edit_or_send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self._next_id = 100

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self._next_id += 1
        self.sent.append(
            {"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
             "reply_markup": reply_markup, "message_id": self._next_id}
        )
        return _StubMsg(self._next_id)

    async def edit_message_text(
        self, text, chat_id=None, message_id=None, parse_mode=None, reply_markup=None
    ):
        self.edits.append(
            {"text": text, "chat_id": chat_id, "message_id": message_id,
             "parse_mode": parse_mode, "reply_markup": reply_markup}
        )
        return True


class _StubMenuMessage:
    def __init__(self) -> None:
        self.edits: list[dict] = []

    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append({"text": text, "reply_markup": reply_markup,
                           "parse_mode": parse_mode})


class _StubCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=5)
        self.bot = _StubBot()
        self.message = _StubMenuMessage()
        self.answers: list[tuple] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


class _FakeModule:
    """Stand-in osint module: canned result or raised error."""

    def __init__(self, *, result=None, error=None, render=None, delay=0.0):
        self._result = result
        self._error = error
        self._render = render
        self._delay = delay

    async def lookup(self, q, cfg, bot=None):
        import asyncio

        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result

    def render(self, data):
        if isinstance(self._render, Exception):
            raise self._render
        return self._render if self._render is not None else "<b>ok</b>"


# ---------------------------------------------------------------- registry --

def test_registry_has_exactly_the_16_toolspec_tools():
    assert len(TOOLS_REGISTRY) == 16
    assert list(TOOLS_REGISTRY) == [t.code for t in toolspec.TOOLS]


def test_registry_modules_imported_and_contract_valid():
    for code, rt in TOOLS_REGISTRY.items():
        assert isinstance(rt, ToolRuntime)
        assert rt.tool is toolspec.BY_CODE[code]
        assert rt.code == code
        assert rt.module.__name__ == f"osint.{rt.tool.module}"
        assert callable(rt.module.lookup)
        assert callable(rt.module.render)


def test_registry_missing_module_fails_fast():
    with pytest.raises(ModuleNotFoundError):
        _load(toolspec.Tool("x", "❌", "X", "x", "definitely_not_a_module"))


def test_registry_broken_contract_fails_fast(monkeypatch):
    fake = types.ModuleType("osint._broken_contract")
    fake.lookup = lambda q, cfg, bot=None: {}  # render() missing
    monkeypatch.setitem(sys.modules, "osint._broken_contract", fake)
    with pytest.raises(RuntimeError, match="render"):
        _load(toolspec.Tool("x", "❌", "X", "x", "_broken_contract"))


# ----------------------------------------------------------------- prompt ---

@pytest.mark.parametrize("code", [t.code for t in toolspec.TOOLS])
def test_prompt_text_contains_title_prompt_and_example(code):
    tool = toolspec.BY_CODE[code]
    text = build_prompt_text(code)
    assert isinstance(text, str)
    assert tool.emoji in text
    assert tool.title in text          # toolspec titles are HTML-safe as-is
    assert tool.prompt in text
    if tool.sample:
        assert f"<code>{html.escape(tool.sample)}</code>" in text
    assert "Example:" in text


def test_prompt_text_escapes_html_interpolations(monkeypatch):
    evil = dataclasses.replace(
        toolspec.BY_CODE["num"],
        title="A<b> & \"Q\"",
        prompt="Type <anything> & go",
        sample="<script>alert(1)</script>",
    )
    monkeypatch.setitem(
        TOOLS_REGISTRY, "num",
        ToolRuntime(tool=evil, module=TOOLS_REGISTRY["num"].module),
    )
    text = build_prompt_text("num")
    assert "A&lt;b&gt; &amp; &quot;Q&quot;" in text
    assert "Type &lt;anything&gt; &amp; go" in text
    assert "&lt;script&gt;" in text and "<script>" not in text


def test_prompt_kb_back_owns_its_own_last_row():
    rows = _rows(build_prompt_kb())
    assert len(rows) == 1
    assert len(rows[0]) == 1
    assert rows[0][0].callback_data == "back:menu"
    assert "Back" in rows[0][0].text
    assert getattr(rows[0][0], "style", None) == PRIMARY


# ------------------------------------------------------------ result kb -----

@pytest.mark.parametrize("code", [t.code for t in toolspec.TOOLS])
def test_result_kb_layout_for_every_tool(code):
    kb = build_result_kb(code, {}, False, query="some-query")
    _assert_layout(_rows(kb))


def test_result_kb_even_module_buttons_chunk_2up():
    result = {"buttons": [("One", "https://a.example"), ("Two", "https://b.example")]}
    rows = _rows(build_result_kb("num", result, False, query="+911234567890"))
    _assert_layout(rows)
    assert [b.url for b in rows[0]] == ["https://a.example", "https://b.example"]
    assert all(b.callback_data is None for b in rows[0])


def test_result_kb_odd_module_button_stands_alone():
    result = {"buttons": [(f"D{i}", f"https://x.example/{i}") for i in range(3)]}
    rows = _rows(build_result_kb("dork", result, False, query="x"))
    _assert_layout(rows)
    assert len(rows[1]) == 1  # lone leftover gets its own row (never the tail)
    assert rows[1][0].url == "https://x.example/2"


def test_result_kb_url_vs_callback_detection():
    result = {"buttons": [
        ("🌐 Web", "https://t.me/x"),
        ("👤 tg", "tg://user?id=42"),
        ("✅ Verify", "vj:check", "success"),
    ]}
    rows = _rows(build_result_kb("tg", result, False, query="@telegram"))
    _assert_layout(rows)
    flat = [b for r in rows for b in r]
    urls = {b.url for b in flat if b.url}
    assert urls == {"https://t.me/x", "tg://user?id=42"}
    verify = next(b for b in flat if b.callback_data == "vj:check")
    assert getattr(verify, "style", None) == SUCCESS


def test_result_kb_drops_malformed_and_oversized_module_buttons():
    result = {"buttons": [
        "not-a-tuple",
        ("NoValue",),
        ("Too long cb", "x" * 65),
        ("Fine", "https://ok.example"),
    ]}
    kb = build_result_kb("ip", result, False, query="8.8.8.8")  # must not raise
    _assert_layout(_rows(kb))
    flat = [b for r in _rows(kb) for b in r]
    assert all(b.url != "x" * 65 for b in flat)
    assert any(b.url == "https://ok.example" for b in flat)


def test_result_kb_again_and_copy_row_exists():
    rows = _rows(build_result_kb("ip", {}, False, query="8.8.8.8"))
    again_row = next(r for r in rows if any(
        b.callback_data == "t:ip" for b in r
    ))
    assert len(again_row) == 2
    again = next(b for b in again_row if b.callback_data == "t:ip")
    copy = next(b for b in again_row if b.callback_data != "t:ip")
    assert "Again" in again.text
    assert "Copy" in copy.text
    if HAS_COPY_TEXT:
        assert copy.copy_text is not None
        assert copy.copy_text.text == "8.8.8.8"
        assert copy.callback_data is None and copy.url is None


def test_result_kb_copy_falls_back_to_result_query():
    rows = _rows(build_result_kb("dork", {"query": "jane@example.com"}, False))
    copy = next(b for r in rows for b in r if "Copy" in b.text)
    if HAS_COPY_TEXT:
        assert copy.copy_text.text == "jane@example.com"


def test_result_kb_partial_note_sits_above_the_tail():
    rows = _rows(build_result_kb("ip", {}, True, query="1.1.1.1"))
    _assert_layout(rows)
    note = rows[-2]
    assert len(note) == 1
    assert note[0].text.startswith("⏱")
    assert note[0].callback_data == "noop"
    assert getattr(note[0], "style", None) == DANGER
    assert rows[-1][0].callback_data == "back:menu"  # Back strictly last


def test_result_kb_without_partial_has_no_note():
    rows = _rows(build_result_kb("ip", {}, False, query="1.1.1.1"))
    assert not any((b.callback_data == "noop") for r in rows for b in r)


# ------------------------------------------------------------ error kb ------

@pytest.mark.parametrize("code", [t.code for t in toolspec.TOOLS])
def test_error_kb_two_full_rows_back_strictly_last(code):
    rows = _rows(build_error_kb(code, "Some Tool", "boom"))
    assert len(rows) == 2
    _assert_layout(rows)
    assert len(rows[0]) == 1
    assert rows[0][0].callback_data == f"t:{code}"
    assert getattr(rows[0][0], "style", None) == DANGER
    assert rows[1][0].callback_data == "back:menu"
    assert getattr(rows[1][0], "style", None) == PRIMARY


def test_error_text_escapes_message_and_keeps_tip():
    text = build_error_text("IP → Info", "bad <input> & stuff")
    assert "⚠️" in text and "IP → Info" in text
    assert "bad &lt;input&gt; &amp; stuff" in text
    assert "<blockquote>" in text
    assert "<i>Tip:" in text


def test_timeout_text_is_friendly_and_names_the_budget():
    text = build_timeout_text("IP → Info")
    assert "⏱" in text and "IP → Info" in text
    assert str(int(LOOKUP_TIMEOUT)) in text
    assert "Tip:" in text


# ---------------------------------------------------------------- routes ----

def test_tools_router_has_callback_and_message_handlers():
    assert len(tools_router.callback_query.handlers) >= 3  # t:, noop, qcopy
    assert len(tools_router.message.handlers) >= 1


def test_callback_handlers_are_wired_with_the_right_filters():
    prompt = _find_handler(tools_router.callback_query, generic.cb_tool_prompt)
    assert prompt.filters[0].callback(_cb_event("t:num")) is True
    assert prompt.filters[0].callback(_cb_event("adm:list")) is False
    assert prompt.filters[0].callback(_cb_event("t")) is False

    noop = _find_handler(tools_router.callback_query, generic.cb_noop)
    assert noop.filters[0].callback(_cb_event("noop")) is True
    assert noop.filters[0].callback(_cb_event("t:num")) is False

    qcopy = _find_handler(tools_router.callback_query, generic.cb_copy_query)
    assert qcopy.filters[0].callback(_cb_event("qcopy:hello world")) is True
    assert qcopy.filters[0].callback(_cb_event("t:num")) is False


def test_message_handler_filters_state_text_and_private():
    handler = _find_handler(tools_router.message, generic.handle_tool_query)
    assert len(handler.filters) == 3  # state + F.text + private chat
    state_f, text_f, chat_f = handler.filters
    assert isinstance(state_f.callback, State)
    assert "waiting_query" in repr(state_f.callback)
    assert text_f.callback(_text_message("hello")) == "hello"
    photo_only = Message(  # no text → F.text never matches
        message_id=2,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        chat=Chat(id=77, type="private"),
    )
    assert text_f.callback(photo_only) is None
    assert chat_f.callback(_text_message("x")) is True
    group_msg = Message(
        message_id=3,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        chat=Chat(id=-100123, type="supergroup"),
        text="x",
    )
    assert chat_f.callback(group_msg) is False  # state flow stays in private chat


def test_all_routers_includes_tools_router():
    routers = all_routers()
    assert tools_router in routers
    assert routers[-1] is tools_router
    names = [r.name for r in routers]
    assert names == ["start", "admin", "joinrequests", "tools"]


def test_toolflow_state_group_defined():
    assert isinstance(ToolFlow.waiting_query, State)
    assert "waiting_query" in repr(ToolFlow.waiting_query)


# ------------------------------------------------------ keyboards extension --

def test_btn_copy_text_builds_clipboard_button():
    b = btn("📋 Copy", copy_text="y")
    assert b.copy_text is not None
    assert b.copy_text.text == "y"
    assert b.callback_data is None and b.url is None


def test_btn_copy_text_is_mutually_exclusive_with_cb_url():
    with pytest.raises(AssertionError):
        btn("X", copy_text="y", cb="t:num")
    with pytest.raises(AssertionError):
        btn("X", copy_text="y", url="https://example.com")


# ------------------------------------------------- handler flows (stubs) ----

async def test_prompt_callback_sets_state_and_sends_prompt_card():
    cb = _StubCallback("t:num")
    state = _fsm()
    await generic.cb_tool_prompt(cb, state)  # type: ignore[arg-type]

    assert cb.answers == [(None, False)]  # spinner cleared, no alert
    assert (await state.get_state()) is not None
    assert (await state.get_state()).endswith("waiting_query")
    assert await state.get_data() == {"code": "num"}

    assert len(cb.message.edits) == 1
    edit = cb.message.edits[0]
    assert "Number → Info" in edit["text"]
    assert edit["parse_mode"] == "HTML"
    _assert_layout(_rows(edit["reply_markup"]))


async def test_prompt_callback_unknown_code_alerts_and_skips_state():
    cb = _StubCallback("t:bogus")
    state = _fsm()
    await generic.cb_tool_prompt(cb, state)  # type: ignore[arg-type]

    assert cb.answers == [("Unknown tool", True)]
    assert await state.get_state() is None
    assert cb.message.edits == []


async def test_prompt_callback_empty_code_alerts():
    # data "t:" → code "" → unknown tool (regression guard: must not raise)
    cb = _StubCallback("t:")
    state = _fsm()
    await generic.cb_tool_prompt(cb, state)  # type: ignore[arg-type]
    assert cb.answers == [("Unknown tool", True)]


async def test_query_empty_text_clears_state_and_sends_error_card():
    state = _fsm()
    await state.set_state(ToolFlow.waiting_query)
    await state.update_data(code="num")
    bot = _StubBot()

    await generic.handle_tool_query(
        _text_message("   "), state, bot, Config(bot_token="123456:TEST")
    )

    assert await state.get_state() is None  # cleared even on failure
    assert bot.edits == []                  # no loader was ever started
    assert len(bot.sent) == 1
    card = bot.sent[0]
    assert "⚠️" in card["text"]
    assert "empty query" in card["text"]
    assert card["parse_mode"] == "HTML"
    _assert_layout(_rows(card["reply_markup"]))


async def test_query_tool_error_stops_loader_and_edits_danger_card(
    monkeypatch,
):
    monkeypatch.setitem(
        TOOLS_REGISTRY, "ip",
        ToolRuntime(
            tool=toolspec.BY_CODE["ip"],
            module=_FakeModule(error=ToolError("bad <input> & stuff")),
        ),
    )
    state = _fsm()
    await state.set_state(ToolFlow.waiting_query)
    await state.update_data(code="ip")
    bot = _StubBot()

    await generic.handle_tool_query(
        _text_message("not an ip"), state, bot, Config(bot_token="123456:TEST")
    )

    assert await state.get_state() is None
    assert bot.sent and bot.sent[0]["text"].startswith("🔍")  # loader appeared
    assert bot.edits, "loading message must be edited with the outcome"
    card = bot.edits[-1]
    assert "⚠️" in card["text"]
    assert "IP → Info" in card["text"]
    assert "bad &lt;input&gt; &amp; stuff" in card["text"]
    assert "<i>Tip:" in card["text"]
    kb = _rows(card["reply_markup"])
    _assert_layout(kb)
    assert kb[0][0].callback_data == "t:ip"
    assert getattr(kb[0][0], "style", None) == DANGER
    # the final edit landed on the ORIGINAL loading message, and it is the
    # LAST edit — the stopped loader can no longer append a frame
    assert card["message_id"] == bot.sent[0]["message_id"]
    assert bot.edits[-1] is card


async def test_query_success_renders_result_with_full_keyboard(monkeypatch):
    monkeypatch.setitem(
        TOOLS_REGISTRY, "num",
        ToolRuntime(
            tool=toolspec.BY_CODE["num"],
            module=_FakeModule(
                result={"query": "+919876543210",
                        "buttons": [("🔎 Source", "https://example.com")]},
                render="<blockquote>RESULT BODY</blockquote>",
            ),
        ),
    )
    state = _fsm()
    await state.set_state(ToolFlow.waiting_query)
    await state.update_data(code="num")
    bot = _StubBot()

    await generic.handle_tool_query(
        _text_message("+919876543210"), state, bot, Config(bot_token="123456:TEST")
    )

    assert await state.get_state() is None
    assert bot.edits, "loading message must be edited with the result"
    final = bot.edits[-1]
    assert final["text"] == "<blockquote>RESULT BODY</blockquote>"
    rows = _rows(final["reply_markup"])
    _assert_layout(rows)
    assert rows[0][0].url == "https://example.com"
    assert not any(b.callback_data == "noop" for r in rows for b in r)  # not partial


async def test_query_timeout_produces_timeout_card(monkeypatch):
    monkeypatch.setattr(generic, "LOOKUP_TIMEOUT", 0.05)
    monkeypatch.setitem(
        TOOLS_REGISTRY, "url",
        ToolRuntime(
            tool=toolspec.BY_CODE["url"],
            module=_FakeModule(delay=30.0),  # cancelled by wait_for
        ),
    )
    state = _fsm()
    await state.set_state(ToolFlow.waiting_query)
    await state.update_data(code="url")
    bot = _StubBot()

    await generic.handle_tool_query(
        _text_message("https://example.com"),
        state,
        bot,
        Config(bot_token="123456:TEST"),
    )

    assert await state.get_state() is None
    card = bot.edits[-1]
    assert "⏱" in card["text"]
    assert "URL → Info" in card["text"]
    _assert_layout(_rows(card["reply_markup"]))


async def test_query_missing_state_code_is_defensive():
    state = _fsm()  # no code armed at all
    bot = _StubBot()
    await generic.handle_tool_query(
        _text_message("hello"), state, bot, Config(bot_token="123456:TEST")
    )
    card = bot.sent[-1]
    assert "⚠️" in card["text"]
    _assert_layout(_rows(card["reply_markup"]))


async def test_noop_and_copy_fallback_callbacks_answer():
    noop = _StubCallback("noop")
    await generic.cb_noop(noop)  # type: ignore[arg-type]
    assert noop.answers == [(None, False)]

    copy = _StubCallback("qcopy:john@example.com")
    await generic.cb_copy_query(copy)  # type: ignore[arg-type]
    assert copy.answers == [("john@example.com", True)]


# ------------------------------------------- cap/strip regressions (M1/L1) ---


def test_cap_message_closes_open_tags_and_stays_under_limit():
    body = "<blockquote><b><i>" + ("data &amp; more " * 500) + "</i></b></blockquote>"
    assert len(body) > 6000
    out = generic._cap_message(body)
    assert len(out) < 4096
    assert "truncated" in out.lower()
    # every opened tag must be closed, in reverse order, before the note
    for tag in ("</i>", "</b>", "</blockquote>"):
        assert out.index(tag) < out.index("truncated")
    # no half tag at the cut point
    assert "<" not in out.rsplit(">", 1)[-1].replace("truncated — output too long", "")


def test_cap_message_drops_trailing_tag_fragment():
    # total > cap so the cut lands INSIDE the trailing "<br>" fragment
    filler = "x" * (generic._MAX_MESSAGE_CHARS - 2)
    out = generic._cap_message(filler + "<br>")
    assert len(filler + "<br>") > generic._MAX_MESSAGE_CHARS
    # the half-open "<b" fragment must be gone entirely (note's own <i> is fine)
    assert "<b" not in out
    assert "truncated" in out.lower()


def test_cap_message_drops_trailing_incomplete_entity():
    # total > cap so the cut lands INSIDE the trailing "&amp" entity
    filler = "y" * (generic._MAX_MESSAGE_CHARS - 2)
    out = generic._cap_message(filler + "&amp")
    pre_note = out.split("truncated")[0]
    # no dangling "&am"/"&amp" survives the cut — ends on plain text
    assert "&" not in pre_note[-10:]
    assert "truncated" in out.lower()


def test_error_text_strips_known_markup_before_escaping():
    text = build_error_text("IP → Info", ToolError('invalid host <code>ex.com</code> & <b>more</b>'))
    assert "ex.com" in text
    assert "&lt;code&gt;" not in text
    assert "&lt;b&gt;" not in text
    # unknown/user angle brackets are still escaped (no HTML injection)
    assert "&lt;input&gt;" in build_error_text("T", ToolError("bad <input>")).replace("&amp;lt;", "&lt;")
