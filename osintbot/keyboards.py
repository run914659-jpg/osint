"""The single keyboard builder (PLAN.md Phase 2 / layout rules D1–D6).

Inline keyboards only — this module never produces reply keyboards.
Layout rules enforced *structurally*:

* ``row2``  → exactly 2 buttons (feature rows are always 2-up).
* ``full``  → 1..N buttons on one row (back/verify/ tails).
* ``menu``  → items chunked 2-up, the Back tail always its own last row.
* ``chain`` → caller rows (2-up) + the tail forced into its own last row,
  so callers *cannot* bury Back/Verify inside a 2-up row.
* ``btn``   → exactly one of ``cb``/``url``, callback ≤ 64 bytes,
  style restricted to ``None``/primary/success/danger.
"""

from __future__ import annotations

import warnings

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pydantic import ValidationError

__all__ = [
    "PRIMARY",
    "SUCCESS",
    "DANGER",
    "MAX_CB_BYTES",
    "btn",
    "row2",
    "full",
    "menu",
    "chain",
    "make_kb",
    "cb",
]

# Style constants (Telegram Bot API button styles: bg primary/success/danger)
PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"

STYLES: frozenset[str | None] = frozenset({None, PRIMARY, SUCCESS, DANGER})

# Telegram hard limit for callback_data
MAX_CB_BYTES = 64

# "danger" is meant for delete/cancel actions only (PLAN.md Phase 2.1:
# lint-style validation). Non-fatal — a warning, so late-phase result
# footers cannot hard-crash the bot.
_DANGER_PREFIXES = ("adm:del", "adm:dely", "adm:deln", "cancel")


def cb(prefix: str, *args) -> str:
    """Join ``prefix`` and ``args`` with ``":"`` and assert ≤ 64 bytes.

    ``cb("adm:ch", chat_id)`` → ``"adm:ch:<chat_id>"``.
    """
    data = ":".join(str(a) for a in (prefix, *args))
    size = len(data.encode("utf-8"))
    assert size <= MAX_CB_BYTES, (
        f"callback_data too long: {size} bytes > {MAX_CB_BYTES}: {data!r}"
    )
    return data


def _build_button(
    text: str,
    *,
    cb_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    kwargs: dict = {"text": text}
    if cb_data is not None:
        kwargs["callback_data"] = cb_data
    if url is not None:
        kwargs["url"] = url
    if style is not None:
        kwargs["style"] = style
    try:
        button = InlineKeyboardButton(**kwargs)
    except (TypeError, ValidationError):
        # aiogram version without a declared `style` field and no extra
        # allowance: build the raw object instead (style rides along).
        button = InlineKeyboardButton.model_construct(**kwargs)
    # Guarantee the style is readable/serializable on every aiogram version.
    if style is not None and getattr(button, "style", None) != style:
        try:
            button.style = style
        except Exception:  # pragma: no cover - defensive
            pass
    return button


def btn(
    text: str,
    *,
    cb: str | None = None,
    url: str | None = None,
    style: str | None = None,
    disabled: bool = False,
) -> InlineKeyboardButton:
    """Build one inline button.

    Exactly one of ``cb``/``url`` must be given. ``disabled=True`` renders
    an inert ``noop`` callback button (aiogram has no ``disabled`` field).
    ``style`` must be one of :data:`PRIMARY`/:data:`SUCCESS`/:data:`DANGER`.
    The callback (UTF-8) must fit Telegram's 64-byte limit.
    """
    if disabled:
        # No disabled field in Bot API → inert noop callback instead.
        cb, url = "noop", None

    assert (cb is None) != (url is None), "btn(): provide exactly one of cb/url"
    assert style in STYLES, f"btn(): invalid style {style!r}; allowed: {sorted(s for s in STYLES if s)}"

    if cb is not None:
        size = len(cb.encode("utf-8"))
        assert size <= MAX_CB_BYTES, (
            f"btn(): callback_data {size} bytes > {MAX_CB_BYTES}: {cb!r}"
        )

    if style == DANGER and cb is not None and not cb.startswith(_DANGER_PREFIXES):
        warnings.warn(
            f"btn(): style='danger' on non-destructive callback {cb!r} "
            "(danger is meant for delete/cancel)",
            stacklevel=2,
        )

    return _build_button(text, cb_data=cb, url=url, style=style)


def row2(a: InlineKeyboardButton, b: InlineKeyboardButton) -> list[InlineKeyboardButton]:
    """Exactly two buttons → one feature row."""
    assert isinstance(a, InlineKeyboardButton) and isinstance(b, InlineKeyboardButton), (
        "row2(): both arguments must be InlineKeyboardButton"
    )
    return [a, b]


def full(*buttons: InlineKeyboardButton) -> list[InlineKeyboardButton]:
    """One full-width row of 1..N buttons (used for back/verify tails)."""
    assert 1 <= len(buttons), "full(): needs at least one button"
    assert all(isinstance(b, InlineKeyboardButton) for b in buttons), (
        "full(): all arguments must be InlineKeyboardButton"
    )
    return list(buttons)


def menu(
    items: list[InlineKeyboardButton],
    *,
    back_cb: str,
    back_text: str = "◀ Back",
) -> list[list[InlineKeyboardButton]]:
    """Chunk ``items`` 2-up and append the Back tail as its own last row.

    An odd leftover item becomes its own single-button row (still never
    merged with the tail).
    """
    assert len(back_cb.encode("utf-8")) <= MAX_CB_BYTES, "menu(): back_cb > 64 bytes"
    rows: list[list[InlineKeyboardButton]] = []
    it = list(items)
    for i in range(0, len(it), 2):
        chunk = it[i : i + 2]
        rows.append(row2(*chunk) if len(chunk) == 2 else full(*chunk))
    rows.append(full(btn(back_text, cb=back_cb, style=PRIMARY)))
    return rows


def chain(
    *rows: list[InlineKeyboardButton],
    tail: InlineKeyboardButton,
) -> list[list[InlineKeyboardButton]]:
    """Append ``tail`` as its own final row — always last, always alone."""
    out: list[list[InlineKeyboardButton]] = []
    for row in rows:
        assert len(row) == 2, (
            f"chain(): non-tail rows must have exactly 2 buttons, got {len(row)}"
        )
        out.append(row2(*row))
    out.append(full(tail))
    return out


def make_kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    """Wrap already-built rows into an InlineKeyboardMarkup."""
    assert len(rows) >= 1, "make_kb(): needs at least one row"
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])
