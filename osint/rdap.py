"""Domain → WHOIS/RDAP (Phase 6, PLAN.md §6.2).

``https://rdap.org/domain/<d>`` is a keyless redirector to the authoritative
registry. Parsed fields: registrar (vCard ``fn``), status values, the three
interesting events (registered / expires / last changed) and nameservers.
"""

from __future__ import annotations

import asyncio
import html
import urllib.parse
from datetime import datetime

import aiohttp

from osint import ToolError

__all__ = ["lookup", "render"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ENDPOINT = "https://rdap.org/domain/{domain}"

_EVENT_LABELS = {
    "registration": "📅 Registered",
    "expiration": "⏳ Expires",
    "last changed": "✏️ Last changed",
}


def _normalize_domain(q: str) -> str:
    raw = (q or "").strip().lower()
    if not raw:
        raise ToolError("Send a domain, e.g. google.com")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    raw = raw.rsplit("@", 1)[-1]
    raw = raw.split(":", 1)[0]
    raw = raw.strip(".")
    if "." not in raw or " " in raw or len(raw) > 253:
        raise ToolError(f"<code>{html.escape((q or '')[:64])}</code> is not a valid domain — send e.g. google.com")
    return raw


def _fmt_date(raw) -> str | None:
    if not raw:
        return None
    text = str(raw)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return text[:10] or None


def _vcard_fn(entity: dict) -> str | None:
    vcard = (entity or {}).get("vcardArray")
    if not vcard or len(vcard) < 2 or vcard[0] != "vcard":
        return None
    for item in vcard[1]:
        if isinstance(item, (list, tuple)) and item and item[0] == "fn" and len(item) >= 4:
            name = str(item[3]).strip()
            if name:
                return name
    return None


def _extract(payload: dict) -> dict:
    registrar = None
    for entity in payload.get("entities") or []:
        if "registrar" in (entity.get("roles") or []):
            registrar = _vcard_fn(entity) or entity.get("handle")
            break

    events: dict[str, str] = {}
    for event in payload.get("events") or []:
        action = str(event.get("eventAction") or "").lower()
        if action in _EVENT_LABELS:
            formatted = _fmt_date(event.get("eventDate"))
            if formatted:
                events[action] = formatted

    nameservers = [
        str(ns.get("ldhName") or "").strip().rstrip(".").lower()
        for ns in payload.get("nameservers") or []
    ]

    statuses = [str(s) for s in (payload.get("status") or payload.get("statuses") or [])]

    return {
        "domain": str(payload.get("ldhName") or payload.get("handle") or "").rstrip(".").lower(),
        "registrar": registrar,
        "statuses": statuses,
        "events": events,
        "nameservers": [ns for ns in nameservers if ns],
    }


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    domain = _normalize_domain(q)
    url = ENDPOINT.format(domain=urllib.parse.quote(domain))
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rdap+json, application/json"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    raise ToolError(
                        f"<code>{html.escape(domain)}</code> was not found in RDAP — "
                        "check the spelling or the TLD"
                    )
                if resp.status == 429:
                    raise ToolError("RDAP rate limit reached — try again in a minute")
                if resp.status >= 400:
                    raise ToolError(f"RDAP answered with HTTP {resp.status} — try again later")
                payload = await asyncio.wait_for(resp.json(content_type=None), 8)
        except asyncio.TimeoutError:
            raise ToolError("RDAP timed out after 8 s — try again") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not reach RDAP: {type(exc).__name__}") from None
    finally:
        await session.close()

    if not isinstance(payload, dict):
        raise ToolError("RDAP returned an unexpected response — try again later")

    data = _extract(payload)
    data["domain"] = data["domain"] or domain
    return data


def render(data: dict) -> str:
    domain = data.get("domain") or "—"
    registrar = data.get("registrar")
    statuses = data.get("statuses") or []
    events = data.get("events") or {}
    nameservers = data.get("nameservers") or []

    status_txt = ", ".join(statuses) if statuses else "no status flags"
    head = (
        f"🧬 <b>{html.escape(str(domain).upper())}</b> — "
        f"{html.escape(registrar or 'unknown registrar')} · {html.escape(status_txt)}"
    )
    lines = [f"<blockquote>{head}</blockquote>"]

    lines.append(
        f"🏢 <b>Registrar:</b> <code>{html.escape(str(registrar))}</code>"
        if registrar
        else "🏢 <b>Registrar:</b> <i>not available</i>"
    )

    for action, label in _EVENT_LABELS.items():
        value = events.get(action)
        lines.append(
            f"{label}: <code>{html.escape(str(value))}</code>"
            if value
            else f"{label}: <i>not available</i>"
        )

    if statuses:
        lines.append("🚩 <b>Status:</b> " + ", ".join(f"<code>{html.escape(s)}</code>" for s in statuses))

    if nameservers:
        body = "\n".join(f"<code>{html.escape(ns)}</code>" for ns in nameservers[:10])
        lines.append(f"🖧 <b>Nameservers ({len(nameservers)}):</b>\n{body}")
    else:
        lines.append("🖧 <b>Nameservers:</b> <i>not available</i>")

    return "\n".join(lines)
