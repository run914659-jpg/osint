"""MAC address → hardware vendor (Phase 6, PLAN.md §6.2).

OUI lookup against the keyless ``https://api.macvendors.com`` (plain-text
response). Accepts ``00:1A:2B:3C:4D:5E``, ``00-1A-2B-3C-4D-5E`` and the
bare 12-hex form; everything else is refused before any request is made.
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
ENDPOINT = "https://api.macvendors.com/{mac}"

_SEP_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_BARE_RE = re.compile(r"^[0-9A-Fa-f]{12}$")


def _normalize(q: str) -> str:
    raw = (q or "").strip()
    if _SEP_RE.match(raw):
        pairs = re.split(r"[:-]", raw)
    elif _BARE_RE.match(raw):
        pairs = [raw[i : i + 2] for i in range(0, 12, 2)]
    else:
        raise ToolError(
            f"<code>{html.escape(raw[:32])}</code> is not a valid MAC address.\n"
            "Send it like 00:1A:2B:3C:4D:5E"
        )
    return ":".join(pair.upper() for pair in pairs)


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    mac = _normalize(q)
    oui = mac[:8]
    headers = {"User-Agent": USER_AGENT, "Accept": "text/plain"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            async with session.get(ENDPOINT.format(mac=mac)) as resp:
                if resp.status == 404:
                    raise ToolError(f"No vendor is registered for OUI <code>{html.escape(oui)}</code>")
                if resp.status == 429:
                    raise ToolError("Vendor API is rate limited — wait a minute and try again")
                if resp.status >= 400:
                    raise ToolError(f"Vendor API answered with HTTP {resp.status} — try again later")
                body = await asyncio.wait_for(resp.content.read(4096), 8)
        except asyncio.TimeoutError:
            raise ToolError("Vendor lookup timed out after 8 s — try again") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not reach the vendor API: {type(exc).__name__}") from None
    finally:
        await session.close()

    vendor = body.decode("utf-8", "replace").strip()
    if not vendor:
        raise ToolError(f"No vendor is registered for OUI <code>{html.escape(oui)}</code>")
    if len(vendor) > 120:
        vendor = vendor[:120] + "…"

    return {"mac": mac, "oui": oui, "vendor": vendor}


def render(data: dict) -> str:
    mac = data.get("mac") or "—"
    oui = data.get("oui") or "—"
    vendor = data.get("vendor") or "unknown"

    head = f"📡 <b>{html.escape(str(vendor))}</b> — OUI {html.escape(str(oui))}"
    lines = [f"<blockquote>{head}</blockquote>"]
    lines.append(f"🏭 <b>Vendor:</b> {html.escape(str(vendor))}")
    lines.append(f"🔢 <b>OUI:</b> <code>{html.escape(str(oui))}</code>")
    lines.append(f"📻 <b>Full MAC:</b> <code>{html.escape(str(mac))}</code>")
    lines.append("\n<i>Vendor = the IEEE-registered prefix owner; cloneable in software, so treat it as a hint.</i>")
    return "\n".join(lines)
