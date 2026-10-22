"""Text → hashes / encodings (fully offline).

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

The input is hashed/encoded verbatim (whitespace matters for hashes); only
blank input is rejected.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import urllib.parse
from html import escape

from osint import ToolError

__all__ = ["lookup", "render", "b64decode"]

_LONG = 80  # values longer than this render in <pre> instead of <code>


def b64decode(text: str) -> str:
    """Standard-base64 decode → UTF-8 text; raise ToolError when impossible."""
    try:
        raw = base64.b64decode(
            text.encode("ascii", errors="strict"), validate=True
        )
    except UnicodeEncodeError:
        raise ToolError("invalid Base64 — non-ASCII characters are not allowed") from None
    except (binascii.Error, ValueError):
        raise ToolError("invalid Base64 — check the padding and characters") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolError("decodes to bytes, but not valid UTF-8 text") from None


def _mono(value: str) -> str:
    escaped = escape(value)
    return f"<pre>{escaped}</pre>" if len(value) > _LONG else f"<code>{escaped}</code>"


async def lookup(q: str, cfg, bot=None) -> dict:
    if not (q or "").strip():
        raise ToolError("send some text to hash or encode")
    text = q  # verbatim — hashes must match the exact bytes the user sent
    blob = text.encode("utf-8")

    data: dict = {
        "text": text,
        "md5": hashlib.md5(blob).hexdigest(),
        "sha1": hashlib.sha1(blob).hexdigest(),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "sha512": hashlib.sha512(blob).hexdigest(),
        "b64": base64.b64encode(blob).decode("ascii"),
        "url": urllib.parse.quote(text, safe=""),
        "url_decoded": urllib.parse.unquote(text),
        "reversed": text[::-1],
        "chars": len(text),
        "words": len(text.split()),
        "lines": text.count("\n") + 1,
        "decode": None,
        "decode_error": None,
    }
    # Base64 decode is best-effort: a bad payload must NOT throw away all the
    # hashes/encodings above, so the ToolError is captured and surfaced inline.
    try:
        data["decode"] = b64decode(text)
    except ToolError as exc:
        data["decode_error"] = str(exc)
    return data


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    preview = str(d.get("text") or "")
    if len(preview) > 48:
        preview = preview[:48] + "…"
    preview = escape(preview)

    chars = d.get("chars", 0)
    words = d.get("words", 0)
    lines_n = d.get("lines", 0)
    lines = [
        f"<blockquote>🔢 {preview}</blockquote>",
        "",
        f"<b>Length:</b> {escape(str(chars))} chars · "
        f"{escape(str(words))} words · {escape(str(lines_n))} lines",
        "",
        "<b>🔐 Hashes</b>",
    ]
    for label, key in (
        ("MD5", "md5"),
        ("SHA-1", "sha1"),
        ("SHA-256", "sha256"),
        ("SHA-512", "sha512"),
    ):
        value = d.get(key)
        if value:
            lines.append(f"<b>{label}:</b> {_mono(str(value))}")

    lines.append("")
    lines.append("<b>🔁 Encodings</b>")
    if d.get("b64"):
        lines.append(f"<b>Base64:</b> {_mono(str(d['b64']))}")
    if d.get("decode") is not None:
        lines.append(f"<b>Base64 decode:</b> {_mono(str(d['decode']))}")
    elif d.get("decode_error"):
        lines.append(
            f"<b>Base64 decode:</b> ⚠️ {escape(str(d['decode_error']))}"
        )
    if d.get("url"):
        lines.append(f"<b>URL encode:</b> {_mono(str(d['url']))}")
    if d.get("url_decoded") is not None:
        lines.append(
            f"<b>URL decode:</b> {_mono(str(d['url_decoded']))}"
        )
    if d.get("reversed") is not None:
        lines.append(f"<b>Reversed:</b> {_mono(str(d['reversed']))}")

    lines.append("")
    lines.append(
        "<span class=\"tg-spoiler\">⚠️ Unsalted MD5/SHA-1 of simple strings "
        "are often reversible — try the Hash → Reverse tool.</span>"
    )
    return "\n".join(lines)
