"""URL → HTTP info (Phase 6, PLAN.md §6.2).

Fetches a URL by hand (``allow_redirects=False``, ≤ 5 hops) so every hop can
be re-checked against the SSRF guard: the host is resolved and *every*
resolved address must be public, otherwise the lookup is refused with a
clear ToolError. Body reads are capped at 512 KB and the ``<title>`` is
pulled out with a regex run in a worker thread.

Known limitation — DNS-rebinding TOCTOU (REVIEW M4): the guard uses a
resolve-then-fetch pattern (``_assert_public_host`` resolves the name, then
aiohttp resolves it *again* when connecting). A DNS-rebinding attacker can
answer the guard's resolution with a public address and the later connection
with a private one. Re-running the guard on every redirect hop narrows but
does not close this window. Documented follow-up: pin the resolved address
into the connection (custom resolver / ``aiohttp.abc.AbstractResolver``
returning the checked IP) instead of resolving twice.

Public helpers re-exported for sibling modules:

* :func:`_assert_public_host` — async SSRF guard (used by ``exiftool``)
* :func:`_ip_blocked` — pure classifier, unit-tested offline
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
import urllib.parse

import aiohttp

from osint import ToolError

__all__ = ["lookup", "render", "_assert_public_host", "_ip_blocked"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_BODY = 512 * 1024        # 512 KB is plenty for a <title>
MAX_HOPS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# --------------------------------------------------------------------------
# input normalization + SSRF guard
# --------------------------------------------------------------------------

def _normalize_url(q: str) -> tuple[str, str, int]:
    """Return ``(url, host, port)``; raise :class:`ToolError` on nonsense."""
    raw = (q or "").strip()
    if not raw:
        raise ToolError("Send a URL, e.g. https://example.com")
    if any(c.isspace() for c in raw):
        raise ToolError("A URL cannot contain spaces — please re-check it")
    if "://" not in raw:
        if "." not in raw:
            raise ToolError(
                "That doesn't look like a URL — send e.g. https://example.com"
            )
        raw = "https://" + raw

    parts = urllib.parse.urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ToolError(f"Only http:// and https:// URLs are supported (got {scheme!r})")
    host = parts.hostname
    if not host:
        raise ToolError("Could not read a hostname from that URL")
    try:
        port = parts.port
    except ValueError:
        raise ToolError("That URL has an invalid port number") from None
    port = port or (443 if scheme == "https" else 80)

    url = urllib.parse.urlunsplit(
        (scheme, parts.netloc, parts.path or "/", parts.query, "")
    )
    return url, host, port


def _ip_blocked(ip) -> bool:
    """True when this address must never be fetched (SSRF classification).

    Accepts a string or a parsed ``ipaddress`` object — pure, sync, offline
    testable.
    """
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 counts as loopback
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_public_host(host: str, port: int) -> str:
    """Resolve ``host`` and refuse private/local targets.

    Returns the first public address (for display); raises
    :class:`ToolError` when *any* resolved address is private — an attacker
    can otherwise point one record at an internal service.
    """
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo, host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
            ),
            5,
        )
    except asyncio.TimeoutError:
        raise ToolError(f"Resolving {host!r} timed out — try again shortly") from None
    except (socket.gaierror, OSError):
        raise ToolError(f"Could not resolve the hostname {host!r}") from None

    addrs = sorted({str(info[4][0]).split("%")[0] for info in infos})
    if not addrs:
        raise ToolError(f"No IP addresses found for {host!r}")

    first_public: str | None = None
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_blocked(ip):
            raise ToolError("private/local addresses are not allowed")
        if first_public is None:
            first_public = str(ip)
    if first_public is None:
        raise ToolError(f"No usable IP addresses found for {host!r}")
    return first_public


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

async def _read_capped(resp: aiohttp.ClientResponse, cap: int) -> tuple[bytes, bool]:
    buf = bytearray()
    truncated = False
    try:
        async for chunk in resp.content.iter_chunked(64 * 1024):
            buf.extend(chunk)
            if len(buf) >= cap:
                truncated = True
                break
    except (aiohttp.ClientError, asyncio.TimeoutError):
        truncated = True
    return bytes(buf[:cap]), truncated


def _extract_title(text: str) -> str | None:
    """Sync regex + unescape on (up to) 512 KB — run via asyncio.to_thread."""
    m = _TITLE_RE.search(text)
    if not m:
        return None
    title = " ".join(html.unescape(m.group(1)).split())
    return title[:200] or None


def _format_chain(chain: list[dict]) -> str:
    """``[{'url':…,'status':…}, …]`` → ``a (301) → b (301) → c (200)``."""
    return " → ".join(f"{hop.get('url', '?')} ({hop.get('status', '?')})" for hop in chain)


async def _probe(session: aiohttp.ClientSession, start_url: str) -> dict:
    chain: list[dict] = []
    current = start_url
    ip = None
    status: int | None = None
    reason = ""
    server = ctype = None
    body = b""
    truncated = False

    for hop in range(MAX_HOPS + 1):
        current, host, port = _normalize_url(current)
        ip = await _assert_public_host(host, port)  # re-checked on every hop

        async with session.get(current, allow_redirects=False) as resp:
            status = resp.status
            reason = resp.reason or ""
            location = resp.headers.get("Location")
            chain.append({"url": current, "status": status})

            if status in _REDIRECTS and location and hop < MAX_HOPS:
                current = urllib.parse.urljoin(current, location)
                continue

            server = resp.headers.get("Server")
            ctype = resp.headers.get("Content-Type")
            body, truncated = await _read_capped(resp, MAX_BODY)
            break

    text = await asyncio.to_thread(body.decode, "utf-8", "replace")
    title = await asyncio.to_thread(_extract_title, text)

    return {
        "url": start_url,
        "final_url": current,
        "status": status,
        "reason": reason,
        "title": title,
        "server": server,
        "content_type": ctype,
        "ip": ip,
        "chain": chain,
        "redirects": max(0, len(chain) - 1),
        "body_bytes": len(body),
        "truncated": truncated,
    }


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    url, _host, _port = _normalize_url(q)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            data = await asyncio.wait_for(_probe(session, url), 8)
        except asyncio.TimeoutError:
            raise ToolError("That URL timed out after 8 s — try another one") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not fetch that URL: {type(exc).__name__}") from None
    finally:
        await session.close()

    data["buttons"] = [("🌐 Open URL", data["final_url"])]
    return data


def render(data: dict) -> str:
    status = data.get("status")
    reason = (data.get("reason") or "").strip()
    final_url = data.get("final_url") or data.get("url") or "—"

    if status is None:
        head = "🔗 <b>No response</b>"
    else:
        pill = f"<b>{status}</b> {html.escape(reason)}" if reason else f"<b>{status}</b>"
        emoji = "🟢" if 200 <= int(status) < 400 else "🔴"
        head = f"{emoji} {pill} — <code>{html.escape(str(final_url))}</code>"

    lines = [f"<blockquote>{head}</blockquote>"]

    chain = data.get("chain") or []
    if len(chain) > 1:
        lines.append(f"⛓ <b>Chain:</b>\n<code>{html.escape(_format_chain(chain))}</code>")
    elif chain:
        lines.append(f"⛓ <b>Request:</b> <code>{html.escape(str(chain[0].get('url', final_url)))}</code>")

    title = data.get("title")
    lines.append(f"📄 <b>Title:</b> {html.escape(title)}" if title else "📄 <b>Title:</b> <i>not available</i>")

    server = data.get("server")
    lines.append(
        f"🖥 <b>Server:</b> <code>{html.escape(str(server))}</code>"
        if server
        else "🖥 <b>Server:</b> <i>not sent</i>"
    )

    ctype = data.get("content_type")
    lines.append(
        f"🧾 <b>Content-Type:</b> <code>{html.escape(str(ctype))}</code>"
        if ctype
        else "🧾 <b>Content-Type:</b> <i>not sent</i>"
    )

    ip = data.get("ip")
    lines.append(
        f"🌍 <b>Resolved IP:</b> <code>{html.escape(str(ip))}</code>"
        if ip
        else "🌍 <b>Resolved IP:</b> <i>not available</i>"
    )

    size = data.get("body_bytes", 0)
    if data.get("truncated"):
        lines.append(f"📏 <b>Body:</b> ≥ {size // 1024} KB (truncated at cap)")
    elif size:
        lines.append(f"📏 <b>Body:</b> {size:,} bytes")

    return "\n".join(lines)
