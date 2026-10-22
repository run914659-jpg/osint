"""Google Dork Builder — turns a name/email/topic into search-operator URLs.

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

Ethical framing: this only *builds* google.com/search URLs over public,
indexed data. No scraping, no authentication bypass, nothing is fetched.
"""

from __future__ import annotations

import urllib.parse
from html import escape

from osint import ToolError

__all__ = ["lookup", "render"]

_GOOGLE = "https://www.google.com/search?q="
_MAX_BUTTONS = 6

_SOCIAL_EXCLUDE = (
    "-site:linkedin.com -site:facebook.com -site:instagram.com "
    "-site:twitter.com -site:x.com"
)


def _looks_like_email(s: str) -> bool:
    return "@" in s and "." in s.rsplit("@", 1)[-1]


def _url(query: str) -> str:
    return _GOOGLE + urllib.parse.quote_plus(query)


async def lookup(q: str, cfg, bot=None) -> dict:
    s = (q or "").strip()
    if len(s) < 3:
        raise ToolError("enter at least 3 characters to search for")
    if len(s) > 100:
        raise ToolError("keep the query under 100 characters")
    # Escape embedded double quotes so the wrapped phrase stays balanced.
    s = s.replace('"', "'")

    core: list[tuple[str, str]] = [
        ("Exact phrase", f'"{s}"'),
        ("No social media", f'"{s}" {_SOCIAL_EXCLUDE}'),
        ("PDF files", f'"{s}" filetype:pdf'),
        ("GitHub code", f'site:github.com "{s}"'),
        ("Page body text", f'intext:"{s}" -site:tiktok.com'),
        ("Twitter / X", f'site:twitter.com "{s}" OR site:x.com "{s}"'),
    ]
    queries: list[tuple[str, str]] = list(core)

    if _looks_like_email(s):
        queries += [
            ("Pastebin dumps", f'site:pastebin.com "{s}"'),
            ("Spreadsheets", f'"{s}" filetype:xlsx OR filetype:csv'),
            ("Everything except FB", f'"{s}" -site:facebook.com'),
        ]
        # Email-specific dorks are the highest-value ones — surface three.
        rank = {"Exact phrase", "Pastebin dumps", "Spreadsheets",
                "No social media", "GitHub code", "Twitter / X"}
    else:
        rank = {label for label, _ in core}

    buttons = [
        (label, _url(query))
        for label, query in queries
        if label in rank
    ][:_MAX_BUTTONS]

    return {
        "query": s,
        "queries": [(label, _url(query)) for label, query in queries],
        "dorks": queries,
        "buttons": buttons[:_MAX_BUTTONS],
    }


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    s = str(d.get("query") or "?")
    dorks = [t for t in (d.get("dorks") or []) if isinstance(t, (tuple, list)) and len(t) >= 2]

    lines = [
        f"<blockquote>🔎 {len(dorks)} ready-made Google dorks for "
        f"“{escape(s)}”</blockquote>",
        "",
    ]
    for i, (label, query) in enumerate(dorks, 1):
        lines.append(f"<b>{i}. {escape(str(label))}</b>")
        lines.append(f"<code>{escape(str(query))}</code>")
        lines.append("")

    lines.append("👆 Tap a button below to run any dork instantly.")
    lines.append(
        "<span class=\"tg-spoiler\">⚠️ Dorks only search PUBLIC indexed "
        "data — use legally.</span>"
    )
    return "\n".join(lines)
