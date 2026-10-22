"""Phone number → info (offline-first via ``phonenumbers``, optional numverify).

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML

Never claims Telegram registration — the Bot API cannot expose that.
"""

from __future__ import annotations

import asyncio
from html import escape

import aiohttp
import phonenumbers
from phonenumbers import carrier, geocoder, timezone

from osint import ToolError

__all__ = ["lookup", "render"]

_HINT = "include country code, e.g. +919876543210"

# PhoneNumberType → human label with a fitting emoji.
_TYPE_LABELS: dict[int, str] = {
    phonenumbers.PhoneNumberType.FIXED_LINE: "☎️ Fixed line",
    phonenumbers.PhoneNumberType.MOBILE: "📱 Mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "📱☎️ Fixed line or mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "📞 Toll-free",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "💰 Premium rate",
    phonenumbers.PhoneNumberType.SHARED_COST: "🔗 Shared cost",
    phonenumbers.PhoneNumberType.VOIP: "🌐 VoIP",
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "📳 Personal number",
    phonenumbers.PhoneNumberType.PAGER: "📟 Pager",
    phonenumbers.PhoneNumberType.UAN: "🏢 UAN (corporate)",
    phonenumbers.PhoneNumberType.VOICEMAIL: "📨 Voicemail",
    phonenumbers.PhoneNumberType.UNKNOWN: "❓ Unknown",
}

# ISO-3166 alpha-2 → name (pycountry-free, common regions only; else show ISO).
_COUNTRY_NAMES: dict[str, str] = {
    "AE": "United Arab Emirates", "AF": "Afghanistan", "AL": "Albania",
    "AR": "Argentina", "AT": "Austria", "AU": "Australia", "BA": "Bosnia & Herzegovina",
    "BD": "Bangladesh", "BE": "Belgium", "BG": "Bulgaria", "BH": "Bahrain",
    "BR": "Brazil", "BY": "Belarus", "CA": "Canada", "CH": "Switzerland",
    "CL": "Chile", "CN": "China", "CO": "Colombia", "CR": "Costa Rica",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark",
    "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia",
    "EG": "Egypt", "ES": "Spain", "ET": "Ethiopia", "FI": "Finland", "FR": "France",
    "GB": "United Kingdom", "GH": "Ghana", "GR": "Greece", "GT": "Guatemala",
    "HK": "Hong Kong", "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia",
    "IE": "Ireland", "IL": "Israel", "IN": "India", "IS": "Iceland", "IT": "Italy",
    "JO": "Jordan", "JP": "Japan", "KE": "Kenya", "KR": "South Korea", "KW": "Kuwait",
    "LK": "Sri Lanka", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "MA": "Morocco", "MC": "Monaco", "ME": "Montenegro", "MK": "North Macedonia",
    "MT": "Malta", "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria",
    "NL": "Netherlands", "NO": "Norway", "NP": "Nepal", "NZ": "New Zealand",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PT": "Portugal",
    "QA": "Qatar", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "SA": "Saudi Arabia", "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
    "SK": "Slovakia", "SM": "San Marino", "TH": "Thailand", "TN": "Tunisia",
    "TR": "Turkey", "TW": "Taiwan", "UA": "Ukraine", "US": "United States",
    "UY": "Uruguay", "VN": "Vietnam", "ZA": "South Africa",
}

_NUMVERIFY_KEYS = (
    "country_name", "location", "carrier", "line_type",
    "local_format", "international_format",
)


def _flag(iso: str) -> str:
    """Regional-indicator flag emoji for an ISO-3166 alpha-2 code."""
    if not iso or len(iso) != 2 or not iso.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in iso)


async def _numverify(number: str, key: str) -> dict | None:
    """Best-effort numverify call. ANY failure → ``None`` (section omitted).

    NOTE: the public demo key is dead — only a real ``NUMVERIFY_API_KEY``
    from the environment produces output.
    """
    url = "https://apilayer.net/api/validate"
    try:
        timeout = aiohttp.ClientTimeout(total=7)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, params={"access_key": key, "number": number}
            ) as resp:
                if resp.status != 200:
                    return None
                payload = await resp.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("success") is False:
        return None
    extra = {
        k: str(payload.get(k))
        for k in _NUMVERIFY_KEYS
        if payload.get(k) not in (None, "")
    }
    return extra or None


async def lookup(q: str, cfg, bot=None) -> dict:
    """Parse/validate a phone number offline; enrich via numverify if keyed."""
    raw = (q or "").strip()
    if not raw:
        raise ToolError(_HINT)
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException:
        raise ToolError(_HINT) from None

    iso = phonenumbers.region_code_for_number(parsed) or ""
    data: dict = {
        "input": raw,
        "valid": bool(phonenumbers.is_valid_number(parsed)),
        "possible": bool(phonenumbers.is_possible_number(parsed)),
        "type": _TYPE_LABELS.get(phonenumbers.number_type(parsed), "❓ Unknown"),
        "region": geocoder.description_for_number(parsed, "en") or "",
        "country_code": iso,
        "country_name": _COUNTRY_NAMES.get(iso, iso or "unknown"),
        "flag": _flag(iso),
        "timezones": list(timezone.time_zones_for_number(parsed)) or [],
        "carrier": carrier.name_for_number(parsed, "en") or "",
        "e164": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        ),
        "national": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        ),
        "international": phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        ),
        "numverify": None,
    }

    key = getattr(cfg, "numverify_api_key", None)
    if key:
        try:
            data["numverify"] = await asyncio.wait_for(
                _numverify(data["e164"], str(key)), 8
            )
        except Exception:
            data["numverify"] = None  # best-effort: omit on any failure
    return data


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    e164 = escape(str(d.get("e164") or d.get("input") or "?"))
    flag = str(d.get("flag") or "🏳️")
    country = str(d.get("country_name") or d.get("country_code") or "unknown")
    iso = str(d.get("country_code") or "")
    type_ = str(d.get("type") or "❓ Unknown")
    valid = bool(d.get("valid"))
    pill = "✅ valid" if valid else "⚠️ invalid"

    lines = [
        f"<blockquote>📱 {e164} — {pill} · {type_} · {flag} "
        f"{escape(country)}</blockquote>",
        "",
    ]

    # Validity ------------------------------------------------------------
    if valid:
        lines.append("<b>Validity:</b> ✅ Valid number")
    elif d.get("possible"):
        lines.append(
            "<b>Validity:</b> ⚠️ Invalid — well-formed but not a real, "
            "assigned number for this region"
        )
    else:
        lines.append("<b>Validity:</b> ⚠️ Invalid number")

    lines.append(f"<b>Type:</b> {type_}")

    region = str(d.get("region") or "")
    if region:
        lines.append(f"<b>Region:</b> 🌍 {escape(region)}")

    country_bit = f"{flag} {escape(country)}"
    if iso:
        country_bit += f" (<code>{escape(iso)}</code>)"
    lines.append(f"<b>Country:</b> {country_bit}")

    carrier_name = str(d.get("carrier") or "")
    lines.append(
        f"<b>Carrier:</b> 📡 {escape(carrier_name)}"
        if carrier_name
        else "<b>Carrier:</b> ❓ unknown"
    )

    zones = [str(z) for z in (d.get("timezones") or []) if z]
    lines.append(
        f"<b>Timezone:</b> 🕐 {escape(', '.join(zones))}"
        if zones
        else "<b>Timezone:</b> ❓ unknown"
    )

    # Formats -------------------------------------------------------------
    lines.append("")
    lines.append("<b>📏 Formats</b>")
    for label, key in (
        ("E.164", "e164"),
        ("National", "national"),
        ("International", "international"),
    ):
        value = d.get(key)
        if value:
            lines.append(f"<b>{label}:</b> <code>{escape(str(value))}</code>")

    # numverify enrichment (only present when a real key + network worked) --
    nv = d.get("numverify")
    if isinstance(nv, dict) and nv:
        lines.append("")
        lines.append("<b>🔎 numverify</b>")
        for k in _NUMVERIFY_KEYS:
            v = nv.get(k)
            if v:
                lines.append(
                    f"<b>{k.replace('_', ' ').title()}:</b> "
                    f"<code>{escape(str(v))}</code>"
                )

    lines.append("")
    lines.append(
        "<span class=\"tg-spoiler\">ℹ️ Carrier & region are best-effort — "
        "ported numbers often show the original operator.</span>"
    )
    return "\n".join(lines)
