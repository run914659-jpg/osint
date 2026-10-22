"""Domain → DNS records (Phase 6, PLAN.md §6.2).

A/AAAA/MX/NS/TXT/CNAME via dnspython's async resolver, one record type at a
time so a failing type (NXDOMAIN, NoAnswer, timeout) degrades to an em dash
instead of killing the whole lookup. Everything is capped at 8 s.
"""

from __future__ import annotations

import asyncio
import html

import dns.asyncresolver
import dns.exception
import dns.resolver

from osint import ToolError

__all__ = ["lookup", "render", "RECORD_TYPES"]

RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")

_ICONS = {
    "A": "🪪",
    "AAAA": "IPv6",
    "MX": "📧",
    "NS": "🌐",
    "TXT": "📝",
    "CNAME": "↪️",
}


def _normalize_domain(q: str) -> str:
    raw = (q or "").strip().lower()
    if not raw:
        raise ToolError("Send a domain, e.g. google.com")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    raw = raw.rsplit("@", 1)[-1]
    raw = raw.split(":", 1)[0]        # strip :port
    raw = raw.strip(".")
    if "." not in raw or " " in raw or len(raw) > 253:
        raise ToolError(f"<code>{html.escape((q or '')[:64])}</code> is not a valid domain — send e.g. google.com")
    return raw


def _clean(value: str) -> str:
    value = str(value).strip()
    if len(value) > 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.rstrip(".")


def _answers_to_values(answer) -> list[str]:
    values: list[str] = []
    for rdata in answer:
        try:
            if hasattr(rdata, "exchange") and hasattr(rdata, "preference"):
                values.append(f"{rdata.preference} {_clean(rdata.exchange)}")
            else:
                values.append(_clean(rdata.to_text()))
        except Exception:                       # pragma: no cover - defensive
            values.append(_clean(str(rdata)))
    return values


async def _resolve_one(name: str, rdtype: str) -> dict:
    try:
        answer = await asyncio.wait_for(
            dns.asyncresolver.resolve(name, rdtype, lifetime=4.0), 8
        )
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return {"ok": False, "values": [], "ttl": None, "note": "no records"}
    except dns.resolver.NoNameservers:
        return {"ok": False, "values": [], "ttl": None, "note": "no usable nameserver"}
    except (dns.exception.Timeout, asyncio.TimeoutError):
        return {"ok": False, "values": [], "ttl": None, "note": "timed out"}
    except Exception as exc:                    # unexpected resolver failure
        return {"ok": False, "values": [], "ttl": None, "note": type(exc).__name__}

    values = await asyncio.to_thread(_answers_to_values, answer)
    ttl = getattr(getattr(answer, "rrset", None), "ttl", None)
    return {"ok": bool(values), "values": values, "ttl": ttl, "note": None}


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    domain = _normalize_domain(q)
    # all six types in parallel: worst case is one 8 s wait, never 6x8 s
    answers = await asyncio.gather(
        *(_resolve_one(domain, rdtype) for rdtype in RECORD_TYPES)
    )
    records = dict(zip(RECORD_TYPES, answers))

    if not any(entry["values"] for entry in records.values()):
        raise ToolError(f"No DNS records found for <code>{html.escape(domain)}</code>")

    return {"domain": domain, "records": records}


def render(data: dict) -> str:
    domain = data.get("domain") or "—"
    records = data.get("records") or {}

    total = sum(len((entry or {}).get("values") or []) for entry in records.values())
    lines = [f"<blockquote>🌍 <b>{html.escape(str(domain))}</b> — {total} record(s) found</blockquote>"]

    for rdtype in RECORD_TYPES:
        entry = records.get(rdtype) or {}
        values = entry.get("values") or []
        icon = _ICONS.get(rdtype, "•")
        if not values:
            note = entry.get("note")
            suffix = f" <i>({html.escape(str(note))})</i>" if note else ""
            lines.append(f"{icon} <b>{rdtype}:</b> —{suffix}")
            continue
        ttl = entry.get("ttl")
        ttl_txt = f" <i>(TTL {ttl}s)</i>" if ttl is not None else ""
        body = "\n".join(f"<code>{html.escape(str(v))}</code>" for v in values[:12])
        if len(values) > 12:
            body += f"\n<i>+{len(values) - 12} more</i>"
        lines.append(f"{icon} <b>{rdtype}:</b>{ttl_txt}\n{body}")

    return "\n".join(lines)
