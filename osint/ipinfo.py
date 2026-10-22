"""IP address → geolocation / network info (Phase 6, PLAN.md §6.2).

Primary source is the free, keyless ``http://ip-api.com`` endpoint. Because
that endpoint is plain-HTTP only and some networks reset port-80 traffic,
two HTTPS fallbacks (``api.ipquery.io`` then ``ipwho.is``) are used when the
primary is unreachable — *API* answers (e.g. "private range") always raise a
ToolError instead of silently falling through.

Everything that blocks (socket/DNS) runs in a worker thread and every
network call is capped with ``asyncio.wait_for(..., 8)``.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress

import aiohttp

from osint import ToolError

__all__ = ["lookup", "render"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PRIMARY = (
    "http://ip-api.com/json/{ip}"
    "?fields=status,message,country,regionName,city,zip,lat,lon,isp,org,as,"
    "query,mobile,proxy,hosting"
)
FALLBACKS = (
    ("ipquery.io", "https://api.ipquery.io/{ip}"),
    ("ipwho.is", "https://ipwho.is/{ip}"),
)

_TRI = {True: "✔️", False: "✖️", None: "—"}


def _validate_ip(q: str) -> str:
    raw = (q or "").strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        raise ToolError(
            f"<code>{html.escape(raw[:64])}</code> is not a valid IP address.\n"
            "Send an IPv4 or IPv6 address, e.g. 8.8.8.8"
        ) from None


async def _get_json(session: aiohttp.ClientSession, url: str) -> dict | list | None:
    async with session.get(url) as resp:
        if resp.status != 200:
            return None
        return await resp.json(content_type=None)


# --------------------------------------------------------------------------
# source adapters → one canonical dict
# --------------------------------------------------------------------------

def _from_ip_api(payload: dict) -> dict | None:
    if payload.get("status") != "success":
        msg = str(payload.get("message") or "unknown error")
        raise ToolError(f"ip-api could not look up that address: {msg}")
    return {
        "ip": payload.get("query"),
        "country": payload.get("country"),
        "region": payload.get("regionName"),
        "city": payload.get("city"),
        "zip": payload.get("zip"),
        "lat": payload.get("lat"),
        "lon": payload.get("lon"),
        "isp": payload.get("isp"),
        "org": payload.get("org"),
        "as": payload.get("as"),
        "mobile": payload.get("mobile"),
        "proxy": payload.get("proxy"),
        "hosting": payload.get("hosting"),
        "source": "ip-api.com",
    }


def _from_ipquery(payload: dict) -> dict | None:
    if not isinstance(payload, dict) or not (payload.get("isp") or payload.get("location")):
        return None
    isp = payload.get("isp") or {}
    loc = payload.get("location") or {}
    risk = payload.get("risk") or {}
    return {
        "ip": payload.get("ip"),
        "country": loc.get("country"),
        "region": loc.get("state"),
        "city": loc.get("city"),
        "zip": loc.get("zipcode"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "isp": isp.get("isp"),
        "org": isp.get("org"),
        "as": isp.get("asn"),
        "mobile": risk.get("is_mobile"),
        "proxy": risk.get("is_proxy") or risk.get("is_vpn") or risk.get("is_tor"),
        "hosting": risk.get("is_datacenter"),
        "source": "ipquery.io",
    }


def _from_ipwho(payload: dict) -> dict | None:
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    conn = payload.get("connection") or {}
    return {
        "ip": payload.get("ip"),
        "country": payload.get("country"),
        "region": payload.get("region"),
        "city": payload.get("city"),
        "zip": payload.get("postal"),
        "lat": payload.get("latitude"),
        "lon": payload.get("longitude"),
        "isp": conn.get("isp"),
        "org": conn.get("org"),
        "as": f"AS{conn.get('asn')}" if conn.get("asn") else None,
        "mobile": None,
        "proxy": None,
        "hosting": None,
        "source": "ipwho.is",
    }


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def _resolve(ip: str, session: aiohttp.ClientSession) -> dict:
    """Try ip-api first, then the HTTPS fallbacks — bounded per source so
    the whole chain still fits inside the caller's 8 s budget."""
    try:
        payload = await asyncio.wait_for(_get_json(session, PRIMARY.format(ip=ip)), 4)
        if isinstance(payload, dict):
            data = _from_ip_api(payload)   # raises ToolError on API-level failure
            if data:
                return data
    except (asyncio.TimeoutError, aiohttp.ClientError):
        # transport-level failure (plain HTTP is blocked on some networks)
        # → fall through to the HTTPS fallbacks
        pass

    for name, template in FALLBACKS:
        try:
            payload = await asyncio.wait_for(
                _get_json(session, template.format(ip=ip)), 4
            )
        except (asyncio.TimeoutError, aiohttp.ClientError):
            continue
        data = _from_ipquery(payload) if name == "ipquery.io" else _from_ipwho(payload)
        if data:
            return data
    raise ToolError("All IP lookup services are unreachable right now — try again shortly")


async def lookup(q: str, cfg, bot=None) -> dict:
    ip = _validate_ip(q)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            return await asyncio.wait_for(_resolve(ip, session), 8)
        except asyncio.TimeoutError:
            raise ToolError("IP lookup timed out after 8 s — try again") from None
    finally:
        await session.close()

    raise ToolError("All IP lookup services are unreachable right now — try again shortly")


def render(data: dict) -> str:
    ip = data.get("ip") or "—"
    country = data.get("country") or "Unknown country"
    city = data.get("city") or "Unknown city"
    region = data.get("region")
    zip_code = data.get("zip")

    place = f"{city}, {region}, {country}" if region else f"{city}, {country}"
    if zip_code:
        place += f" ({zip_code})"
    head = f"🌍 <b>{html.escape(str(country))}</b> — {html.escape(place)}"

    lines = [f"<blockquote>{head}</blockquote>", f"🌐 <b>IP:</b> <code>{html.escape(str(ip))}</code>"]
    lines.append(f"📍 <b>Location:</b> {html.escape(place)}")

    lat, lon = data.get("lat"), data.get("lon")
    if lat is not None and lon is not None:
        lines.append(f"🧭 <b>Coordinates:</b> <code>{lat}, {lon}</code>")

    for label, key in (("🏢 ISP", "isp"), ("🏛 Org", "org"), ("🔢 AS", "as")):
        value = data.get(key)
        lines.append(
            f"{label}: <code>{html.escape(str(value))}</code>" if value else f"{label}: <i>not available</i>"
        )

    for label, key in (("📱 Mobile", "mobile"), ("🛡 Proxy/VPN", "proxy"), ("🖥 Hosting/DC", "hosting")):
        flag = _TRI.get(data.get(key), "—")
        lines.append(f"{label}: {flag}")

    source = data.get("source")
    if source:
        lines.append(f"\n<i>Source: {html.escape(str(source))}</i>")

    return "\n".join(lines)
