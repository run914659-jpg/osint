"""Email → info: syntax, MX records, disposable check, Gravatar, SMTP reach.

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

All network work runs in threads with an 8 s budget. Wording stays honest:
"not verified" never becomes "invalid".
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import urllib.error
import urllib.request

import dns.exception
import dns.resolver

from osint import ToolError

__all__ = ["lookup", "render", "is_valid", "is_disposable", "gravatar_hash"]

# RFC-ish: exactly one "@", local part without "@", domain needs a dot + TLD.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Well-known disposable / throwaway providers (kept to recognizable ones).
DISPOSABLE_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com", "10minutemail.net", "1secmail.com", "33mail.com",
    "anonymbox.com", "crazymailing.com", "dispostable.com", "dropmail.me",
    "emailfake.com", "emailondeck.com", "fakeinbox.com", "getnada.com",
    "guerrillamail.com", "guerrillamail.info", "guerrillamailblock.com",
    "harakirimail.com", "jetable.org", "mailcatch.com", "maildrop.cc",
    "mailexpire.com", "mailinator.com", "mailinator.net", "mailsac.com",
    "mailnesia.com", "meltmail.com", "minutemail.com", "moakt.com",
    "mohmal.com", "mytemp.email", "mytrashmail.com", "sharklasers.com",
    "spambog.com", "spambog.de", "spambog.ru", "temp-mail.org", "tempail.com",
    "tempinbox.com", "tempr.email", "tempmail.com", "throwaway.email",
    "throwawaymail.com", "trash-mail.com", "trashmail.com", "trashmail.de",
    "wegwerfemail.de", "yopmail.com", "yopmail.fr",
})


def is_valid(email: str) -> bool:
    """True when the address matches the bundled RFC-ish regex."""
    return bool(_EMAIL_RE.match((email or "").strip()))


def is_disposable(domain: str) -> bool:
    """True for a known disposable domain or any of its subdomains."""
    d = (domain or "").strip().lower().rstrip(".")
    if not d:
        return False
    if d in DISPOSABLE_DOMAINS:
        return True
    return any(d.endswith("." + known) for known in DISPOSABLE_DOMAINS)


def gravatar_hash(email: str) -> str:
    """MD5 of the lowercased address — Gravatar's documented lookup key."""
    return hashlib.md5((email or "").strip().lower().encode("utf-8")).hexdigest()


def _lookup_mx(domain: str) -> tuple[list[tuple[int, str]], str | None]:
    """(sorted [(priority, host)], note). Raises ToolError on dead domains."""
    resolver = dns.resolver.Resolver(configure=True)
    resolver.lifetime = 5.0
    try:
        answers = resolver.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        raise ToolError("domain does not exist") from None
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        try:
            resolver.resolve(domain, "A")
        except dns.resolver.NXDOMAIN:
            raise ToolError("domain does not exist") from None
        except Exception:
            return [], "⚠️ No MX record found for this domain."
        return [], (
            "⚠️ No MX record — mail may fall back to the domain's A record."
        )
    except (dns.exception.Timeout, TimeoutError, OSError):
        raise ToolError("DNS lookup timed out — try again") from None
    except Exception:
        raise ToolError("DNS lookup failed for this domain") from None

    records: list[tuple[int, str]] = []
    null_mx = False
    for r in answers:
        try:
            host = str(r.exchange).rstrip(".")
            prio = int(r.preference)
        except Exception:
            continue
        if not host:
            null_mx = True
            continue
        records.append((prio, host))
    records.sort()
    if null_mx:
        return [], "⚠️ Domain publishes a null MX — it explicitly refuses email."
    if not records:
        return [], "⚠️ No usable MX record found."
    return records, None


def _gravatar_exists(avatar_hash: str) -> bool | None:
    """200 → True, 404 → False, network trouble → None (unknown)."""
    url = f"https://www.gravatar.com/avatar/{avatar_hash}?d=404&s=64"
    req = urllib.request.Request(url, headers={"User-Agent": "osintbot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code == 200
    except Exception:
        return None


def _smtp_reachable(host: str) -> bool:
    """Plain TCP connect to port 25 — success means the server answers."""
    try:
        with socket.create_connection((host, 25), timeout=3):
            return True
    except OSError:
        return False


async def lookup(q: str, cfg, bot=None) -> dict:
    raw = (q or "").strip()
    if not is_valid(raw):
        raise ToolError("invalid email format")

    local, domain = raw.rsplit("@", 1)
    lower = raw.lower()

    try:
        mx, mx_note = await asyncio.wait_for(
            asyncio.to_thread(_lookup_mx, domain.lower()), 8
        )
    except TimeoutError:
        raise ToolError("DNS lookup timed out — try again") from None

    avatar_hash = gravatar_hash(raw)
    first_mx = mx[0][1] if mx else None

    tasks = [asyncio.wait_for(asyncio.to_thread(_gravatar_exists, avatar_hash), 8)]
    if first_mx:
        tasks.append(
            asyncio.wait_for(asyncio.to_thread(_smtp_reachable, first_mx), 8)
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)

    gravatar = results[0] if isinstance(results[0], bool) else None
    if first_mx:
        smtp: str | None = (
            "reachable" if results[1] is True else "blocked"
        )
    else:
        smtp = None

    data: dict = {
        "email": lower,
        "local": local,
        "domain": domain.lower(),
        "mx": mx,
        "mx_note": mx_note,
        "disposable": is_disposable(domain),
        "gravatar": gravatar,
        "gravatar_hash": avatar_hash,
        "gravatar_preview": f"https://www.gravatar.com/avatar/{avatar_hash}?s=96&d=identicon",
        "smtp": smtp,
        "smtp_host": first_mx,
    }
    if gravatar is True:
        data["buttons"] = [("🖼️ Open Gravatar", f"https://www.gravatar.com/{avatar_hash}")]
    return data


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    email = str(d.get("email") or "?")
    domain = str(d.get("domain") or "?")
    local = str(d.get("local") or "?")
    mx = [t for t in (d.get("mx") or []) if isinstance(t, (tuple, list)) and len(t) >= 2]

    summary = (
        f"📧 {escape(email)} — ✅ valid format · "
        f"{len(mx)} mail server{'s' if len(mx) != 1 else ''}"
    )
    lines = [f"<blockquote>{summary}</blockquote>", ""]

    lines.append("<b>Format:</b> ✅ Valid email syntax")
    lines.append(f"<b>Local part:</b> <code>{escape(local)}</code>")
    lines.append(f"<b>Domain:</b> <code>{escape(domain)}</code>")

    lines.append("<b>MX records:</b>")
    if mx:
        for prio, host in mx:
            lines.append(f"  <code>{escape(str(prio))} {escape(str(host))}</code>")
    else:
        lines.append("  ❌ none found")
    note = d.get("mx_note")
    if note:
        lines.append(f"  {escape(str(note))}")

    if d.get("disposable"):
        lines.append(
            "<b>Disposable:</b> ⚠️ Yes — this is a throwaway / temp-mail provider"
        )
    else:
        lines.append("<b>Disposable:</b> ✔ Not a known disposable domain")

    grav = d.get("gravatar")
    preview = d.get("gravatar_preview")
    if grav is True:
        lines.append("<b>Gravatar:</b> ✅ Profile exists")
        if preview:
            lines.append(f"<b>Preview:</b> <code>{escape(str(preview))}</code>")
    elif grav is False:
        lines.append("<b>Gravatar:</b> ❌ No Gravatar profile")
    else:
        lines.append("<b>Gravatar:</b> ❓ unknown (network blocked)")

    smtp = d.get("smtp")
    host = str(d.get("smtp_host") or "")
    if smtp == "reachable":
        lines.append(
            f"<b>SMTP:</b> ✅ reachable on port 25 "
            f"(<code>{escape(host)}</code>)"
        )
    elif smtp == "blocked":
        lines.append(
            "<b>SMTP:</b> ⚠️ not verified (blocked/timeout) — the domain may "
            "still accept mail"
        )
    else:
        lines.append("<b>SMTP:</b> ⏭️ skipped (no MX server to test)")

    return "\n".join(lines)
