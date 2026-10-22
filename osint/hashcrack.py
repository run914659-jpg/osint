"""MD5 hash → best-effort reverse lookup (Phase 6, PLAN.md §6.2).

Only plain 32-hex MD5 is accepted. The dictionary is
``https://md5.gromweb.com`` (keyless; verified live 2026-10):

* the form submits ``?md5=<hash>`` — ``?query=`` is ignored by the site and
  returns the plain homepage, so the ``md5`` parameter is used here;
* a hit reads ``… was successfully reversed into the string <a class="String">…``
  (the page has **no** ``id="hash_string"`` element — that pattern is still
  checked first in case the layout changes back);
* a miss reads ``Provided MD5 hash could not be reversed …``.

MD5 is broken for security purposes — the result says so, in the render.
"""

from __future__ import annotations

import asyncio
import html
import re

import aiohttp

from osint import ToolError

__all__ = ["lookup", "render"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ENDPOINT = "https://md5.gromweb.com/?md5={hash}"
MAX_BODY = 512 * 1024

_MD5_RE = re.compile(r"^[a-fA-F0-9]{32}$")

# Ordered: most specific result markup first.
_FOUND_PATTERNS = (
    re.compile(r'id=["\']hash_string["\'][^>]*>([^<]+)<', re.IGNORECASE),
    re.compile(
        r"was successfully reversed into the string\s*<a[^>]*>([^<]+)</a>",
        re.IGNORECASE,
    ),
    re.compile(
        r'class=["\']word-break-all["\'][^>]*>.*?class=["\']String["\'][^>]*>([^<]+)</a>',
        re.IGNORECASE | re.DOTALL,
    ),
)
_NOT_FOUND_RE = re.compile(
    r"could not be reversed|no reverse string was found|hash not found|not found",
    re.IGNORECASE,
)
_KNOWN_SUMS_RE = re.compile(r"Known MD5 sums:\s*([\d,\.]+)")


def _validate(q: str) -> str:
    raw = (q or "").strip()
    if len(raw.replace(" ", "")) > 32:
        raise ToolError(
            "Only MD5 (32 hex characters) is supported for reverse lookup — "
            f"you sent {len(raw.replace(' ', ''))} characters."
        )
    if not _MD5_RE.match(raw):
        raise ToolError(
            "Only MD5 (32 hex chars) supported for reverse — "
            "send just the hash, e.g. 5f4dcc3b5aa765d61d8327deb882cf99"
        )
    return raw.lower()


def _parse_page(text: str, digest: str) -> tuple[bool, str | None, str | None]:
    """→ ``(found, plaintext, known_sum_count)``."""
    known = None
    m = _KNOWN_SUMS_RE.search(text)
    if m:
        known = m.group(1).strip()

    # Negative phrasing wins: the page's own explanation section contains
    # <a class="String">example</a> links that must never count as a hit.
    if _NOT_FOUND_RE.search(text):
        return False, None, known

    for pattern in _FOUND_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        plaintext = html.unescape(m.group(1)).strip()
        if plaintext and plaintext.lower() != digest.lower():
            return True, plaintext, known
    return False, None, known


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    digest = _validate(q)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            async with session.get(ENDPOINT.format(hash=digest)) as resp:
                if resp.status == 429:
                    raise ToolError("Reverse-lookup service is rate limited — try again shortly")
                if resp.status >= 400:
                    raise ToolError(f"Reverse-lookup service answered with HTTP {resp.status}")
                body = await asyncio.wait_for(resp.content.read(MAX_BODY), 8)
        except asyncio.TimeoutError:
            raise ToolError("Reverse lookup timed out after 8 s — try again") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not reach the reverse-lookup service: {type(exc).__name__}") from None
    finally:
        await session.close()

    text = body.decode("utf-8", "replace")
    found, plaintext, known = await asyncio.to_thread(_parse_page, text, digest)

    return {
        "hash": digest,
        "algo": "MD5",
        "length": 32,
        "found": found,
        "plaintext": plaintext,
        "known_sums": known,
        "source": "md5.gromweb.com",
    }


def render(data: dict) -> str:
    digest = data.get("hash") or "—"
    head = f"🔑 <b>MD5</b> · 32 hex chars · <code>{html.escape(str(digest))}</code>"
    lines = [f"<blockquote>{head}</blockquote>"]

    if data.get("found"):
        plain = str(data.get("plaintext") or "")
        safe = html.escape(plain)
        lines.append("✅ <b>Reversed!</b> Original string:")
        lines.append(f'<span class="tg-spoiler"><code>{safe}</code></span>')
        lines.append(f"\n🔓 <b>Plaintext:</b> <code>{safe}</code>")
        lines.append("\n⚠️ MD5 is broken for passwords/signatures — treat this as a dictionary hit only.")
    else:
        known = data.get("known_sums")
        scope = f" of {known}+ entries" if known else ""
        lines.append("❌ <b>Not reversed</b>")
        lines.append(
            f"The hash is not in the dictionary{scope} — it is either a rare string, "
            "a salted hash, or another algorithm wearing a 32-hex costume."
        )
        lines.append("\n💡 Tip: MD5 of a single word/short password is usually in there; long or salted values rarely are.")

    lines.append(f"\n<i>Source: {html.escape(str(data.get('source') or 'md5.gromweb.com'))}</i>")
    return "\n".join(lines)
