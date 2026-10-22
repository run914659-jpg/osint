"""Image URL → EXIF metadata (Phase 6, PLAN.md §6.2).

Downloads an image (≤ 5 MB, manual redirects so the SSRF guard runs on every
hop), checks the content type / magic bytes, then hands the bytes to Pillow
in a worker thread for ``getexif()`` parsing.

Reported tags: DateTime, Make, Model, Software, Artist, XPTitle/XPAuthor and
GPS *presence* only — this tool deliberately never claims coordinates it
would have to guess at.
"""

from __future__ import annotations

import asyncio
import html
import io
import urllib.parse

import aiohttp
from PIL import Image

from osint import ToolError
from osint.urlinfo import _assert_public_host, _normalize_url

__all__ = ["lookup", "render"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_IMAGE = 5 * 1024 * 1024      # 5 MB hard cap
MAX_HOPS = 3
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

# Pillow EXIF tag ids (numbers keep it dependency-light and explicit)
TAGS: dict[int, str] = {
    306: "DateTime",
    271: "Make",
    272: "Model",
    305: "Software",
    315: "Artist",
    0x9C9B: "XPTitle",
    0x9C9C: "XPAuthor",
}
GPS_IFD = 0x8825

_MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
)


def _looks_like_image(head: bytes, content_type: str) -> str | None:
    """Return the format implied by magic bytes / content type (or None)."""
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype.startswith("image/"):
        return ctype.split("/", 1)[1]
    return None


def _decode_xp(value):
    """XP* tags arrive as NUL-terminated UTF-16LE bytes."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-16-le", errors="replace").rstrip("\x00").strip()
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _probe(data: bytes) -> dict:
    """Blocking Pillow work — always run via asyncio.to_thread()."""
    img = Image.open(io.BytesIO(data))
    fmt = img.format or "unknown"
    width, height = img.size

    exif = img.getexif()
    fields: dict[str, str] = {}
    for tag, label in TAGS.items():
        raw = exif.get(tag)
        if raw is None:
            continue
        if tag in (0x9C9B, 0x9C9C):
            text = _decode_xp(raw)
        else:
            text = str(raw).strip()
        if text:
            fields[label] = text

    gps: dict = {}
    if exif:
        try:
            gps = dict(exif.get_ifd(GPS_IFD) or {})
        except Exception:                       # pragma: no cover - defensive
            gps = {}

    gps_version = None
    if gps:
        raw_version = gps.get(0)
        if raw_version is not None:
            gps_version = ".".join(
                str(b) for b in raw_version
            ) if isinstance(raw_version, (bytes, bytearray)) else str(raw_version)

    return {
        "format": fmt,
        "width": width,
        "height": height,
        "fields": fields,
        "gps": bool(gps),
        "gps_version": gps_version,
        "has_exif": bool(fields) or bool(gps),
    }


async def _download(session: aiohttp.ClientSession, start_url: str) -> tuple[bytes, str, str]:
    """Follow ≤ MAX_HOPS redirects, re-running the SSRF guard on each hop."""
    current = start_url
    content_type = ""
    for hop in range(MAX_HOPS + 1):
        current, host, port = _normalize_url(current)
        await _assert_public_host(host, port)   # never fetch internal hosts

        async with session.get(current, allow_redirects=False) as resp:
            if resp.status >= 400:
                raise ToolError(f"The image server answered with HTTP {resp.status}")
            location = resp.headers.get("Location")
            if resp.status in _REDIRECTS and location and hop < MAX_HOPS:
                current = urllib.parse.urljoin(current, location)
                continue

            content_type = resp.headers.get("Content-Type", "")
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_IMAGE:
                    raise ToolError("That image is larger than the 5 MB limit — send a smaller one")
            final_url = str(resp.url)
            return bytes(buf), content_type, final_url

    raise ToolError("Too many redirects while fetching that image")


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

def _validate_input(q: str) -> str:
    """Require a real http(s) URL — a bare filename is not enough."""
    raw = (q or "").strip()
    if "://" not in raw:
        raise ToolError(
            "Send a full image URL, e.g. https://example.com/photo.jpg "
            "(jpg / png / webp)"
        )
    url, _host, _port = _normalize_url(raw)
    return url


async def lookup(q: str, cfg, bot=None) -> dict:
    url = _validate_input(q)

    headers = {"User-Agent": USER_AGENT, "Accept": "image/*,*/*"}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), headers=headers
    )
    try:
        try:
            data, content_type, final_url = await asyncio.wait_for(_download(session, url), 8)
        except asyncio.TimeoutError:
            raise ToolError("Downloading that image timed out after 8 s — try another URL") from None
        except aiohttp.ClientError as exc:
            raise ToolError(f"Could not download the image: {type(exc).__name__}") from None
    finally:
        await session.close()

    if not data:
        raise ToolError("That URL returned an empty body — not an image")

    kind = await asyncio.to_thread(_looks_like_image, data[:16], content_type)
    if kind is None:
        raise ToolError(
            "That URL is not an image "
            f"(content-type: {html.escape((content_type or 'unknown')[:60])})"
        )

    try:
        info = await asyncio.to_thread(_probe, data)
    except Exception as exc:
        raise ToolError(f"Could not parse that image: {type(exc).__name__}") from None

    info.update({"url": final_url, "bytes": len(data), "detected": kind})
    return info


def render(data: dict) -> str:
    url = data.get("url") or "—"
    fmt = data.get("format") or data.get("detected") or "?"
    width, height = data.get("width"), data.get("height")
    size = f"{width}×{height}" if width and height else "unknown size"

    head = (
        f"🖼 <b>{html.escape(str(fmt))}</b> · {html.escape(str(size))} · "
        f"{(data.get('bytes') or 0) // 1024} KB"
    )
    lines = [f"<blockquote>{head}</blockquote>", f"🔗 <b>File:</b> <code>{html.escape(str(url))}</code>"]

    fields = data.get("fields") or {}
    icons = {
        "DateTime": "🕒",
        "Make": "🏭",
        "Model": "📱",
        "Software": "🧰",
        "Artist": "✍️",
        "XPTitle": "🏷",
        "XPAuthor": "👤",
    }
    for label in TAGS.values():
        value = fields.get(label)
        if value:
            lines.append(f"{icons.get(label, '•')} <b>{label}:</b> <code>{html.escape(str(value))}</code>")

    if data.get("gps"):
        version = data.get("gps_version")
        note = f" · v{html.escape(str(version))}" if version else ""
        lines.append(f"🛰 <b>GPS block:</b> present{note} (location tags stored in the file)")
    else:
        lines.append("🛰 <b>GPS block:</b> not present")

    if not data.get("has_exif"):
        lines.append(
            "\n❌ <b>No EXIF metadata</b> (image likely cleaned/re-saved — "
            "phones and messengers often strip it on upload)."
        )
    else:
        lines.append(
            "\n⚠️ EXIF is written by the camera/app, not the OS — treat it as a lead, not proof."
        )

    return "\n".join(lines)
