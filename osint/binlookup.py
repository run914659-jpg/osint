"""Card BIN → issuer info (Phase 6, PLAN.md §6.2).

Strictly 6–8 **digits** (the IIN/BIN only) — anything longer is refused with
an explicit "don't send a full card number" message, so this tool can never
be fed a PAN. Data: ``https://lookup.binlist.net`` (keyless, ~30 req/min;
optional ``cfg.binlist_api_key`` raises the limit via the ``scp-api-key``
header).
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
ENDPOINT = "https://lookup.binlist.net/{bin}"

_DIGITS_RE = re.compile(r"^[0-9]{6,8}$")


def _validate(q: str) -> str:
    digits = "".join((q or "").split())
    if len(digits) > 8:
        raise ToolError(
            "Send only the first 6-8 digits (BIN), not the full number — "
            "this tool never accepts complete card numbers."
        )
    if not _DIGITS_RE.match(digits):
        raise ToolError(
            f"<code>{html.escape(digits[:16])}</code> is not a valid BIN.\n"
            "Send the first 6-8 digits of the card, e.g. 45717300"
        )
    return digits


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    bin_number = _validate(q)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    api_key = getattr(cfg, "binlist_api_key", None)
    if api_key:
        headers["scp-api-key"] = str(api_key)

    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            async with session.get(ENDPOINT.format(bin=bin_number)) as resp:
                if resp.status == 404:
                    raise ToolError(
                        f"BIN <code>{html.escape(bin_number)}</code> was not found — "
                        "check the digits (try the first 6 or 8)."
                    )
                if resp.status == 429:
                    raise ToolError(
                        "BIN lookup is rate limited right now — wait a minute and try again."
                    )
                if resp.status >= 400:
                    raise ToolError(f"BIN service answered with HTTP {resp.status} — try again later")
                payload = await asyncio.wait_for(resp.json(content_type=None), 8)
        except asyncio.TimeoutError:
            raise ToolError("BIN lookup timed out after 8 s — try again") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not reach the BIN service: {type(exc).__name__}") from None
    finally:
        await session.close()

    if not isinstance(payload, dict):
        raise ToolError("BIN service returned an unexpected response — try again later")

    bank = payload.get("bank") or None
    country = payload.get("country") or None
    return {
        "bin": bin_number,
        "scheme": payload.get("scheme"),
        "type": payload.get("type"),
        "brand": payload.get("brand"),
        "name": payload.get("name"),
        "bank": {
            "name": bank.get("name"),
            "city": bank.get("city"),
            "url": bank.get("url"),
            "phone": bank.get("phone"),
        } if isinstance(bank, dict) else None,
        "country": {
            "name": country.get("name"),
            "emoji": country.get("emoji"),
            "currency": country.get("currency"),
            "alpha2": country.get("alpha2"),
        } if isinstance(country, dict) else None,
    }


def render(data: dict) -> str:
    bin_number = data.get("bin") or "—"
    scheme = (data.get("scheme") or "?").upper()
    card_type = data.get("type") or "unknown type"
    brand = data.get("brand") or "unknown brand"

    head = f"💳 <b>{html.escape(str(bin_number))}</b> — {html.escape(scheme)} {html.escape(str(card_type))}"
    lines = [f"<blockquote>{head}</blockquote>"]

    lines.append(f"🏷 <b>Brand:</b> <code>{html.escape(str(brand))}</code>")
    if data.get("name"):
        lines.append(f"📝 <b>Card level:</b> {html.escape(str(data['name']))}")
    lines.append(f"🆔 <b>Scheme:</b> <code>{html.escape(scheme)}</code> · <b>Type:</b> {html.escape(str(card_type))}")

    bank = data.get("bank")
    if bank and any(bank.values()):
        parts = [str(bank.get(k)) for k in ("city", "name") if bank.get(k)]
        lines.append(f"🏦 <b>Bank:</b> {html.escape(', '.join(parts)) if parts else '<i>not available</i>'}")
        if bank.get("url"):
            url = str(bank["url"])
            href = url if url.startswith(("http://", "https://")) else f"https://{url}"
            lines.append(f"🏦 <b>Bank site:</b> <a href=\"{html.escape(href, quote=True)}\">{html.escape(url)}</a>")
        if bank.get("phone"):
            lines.append(f"☎️ <b>Bank phone:</b> <code>{html.escape(str(bank['phone']))}</code>")
    else:
        lines.append("🏦 <b>Bank:</b> <i>not available</i>")

    country = data.get("country")
    if country and any(country.values()):
        emoji = str(country.get("emoji") or "🌍")
        name = country.get("name") or "unknown"
        line = f"🗺 <b>Country:</b> {emoji} {html.escape(str(name))}"
        if country.get("currency"):
            line += f" · {html.escape(str(country['currency']))}"
        lines.append(line)
    else:
        lines.append("🗺 <b>Country:</b> <i>not available</i>")

    lines.append("\n⚠️ A BIN tells you the issuer, never the holder — and never send full card numbers here.")
    return "\n".join(lines)
