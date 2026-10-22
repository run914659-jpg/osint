"""IBAN → validation (fully offline: format, country length, mod-97).

Contract (osintbot/toolspec.py)::

    async def lookup(q, cfg, bot=None) -> dict
    def render(data) -> str        # HTML
"""

from __future__ import annotations

import re
from html import escape

from osint import ToolError

__all__ = ["lookup", "render", "normalize"]

_HINT = "send an IBAN, e.g. DE89370400440532013000"
_FORMAT_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")

# ISO-3166 alpha-2 → official IBAN length (SWIFT registry, common set).
_COUNTRY_LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24,
    "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23,
    "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32,
    "LI": 21, "LT": 20, "LU": 20, "LV": 21, "LY": 25, "MC": 27, "MD": 24,
    "ME": 22, "MK": 19, "MN": 20, "MR": 27, "MT": 31, "MU": 30, "NL": 18,
    "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24,
    "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
    "ST": 25, "SV": 32, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22,
    "VG": 24, "XK": 20,
}

_COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AL": "Albania",
    "AT": "Austria", "AZ": "Azerbaijan", "BA": "Bosnia & Herzegovina",
    "BE": "Belgium", "BG": "Bulgaria", "BH": "Bahrain", "BR": "Brazil",
    "BY": "Belarus", "CH": "Switzerland", "CR": "Costa Rica", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "DO": "Dominican Republic",
    "EE": "Estonia", "EG": "Egypt", "ES": "Spain", "FI": "Finland",
    "FO": "Faroe Islands", "FR": "France", "GB": "United Kingdom", "GE": "Georgia",
    "GI": "Gibraltar", "GL": "Greenland", "GR": "Greece", "GT": "Guatemala",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IL": "Israel",
    "IQ": "Iraq", "IS": "Iceland", "IT": "Italy", "JO": "Jordan", "KW": "Kuwait",
    "KZ": "Kazakhstan", "LB": "Lebanon", "LC": "Saint Lucia", "LI": "Liechtenstein",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya",
    "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MK": "North Macedonia",
    "MN": "Mongolia", "MR": "Mauritania", "MT": "Malta", "MU": "Mauritius",
    "NL": "Netherlands", "NO": "Norway", "PK": "Pakistan", "PL": "Poland",
    "PS": "Palestine", "PT": "Portugal", "QA": "Qatar", "RO": "Romania",
    "RS": "Serbia", "SA": "Saudi Arabia", "SC": "Seychelles", "SE": "Sweden",
    "SI": "Slovenia", "SK": "Slovakia", "SM": "San Marino", "ST": "São Tomé",
    "SV": "El Salvador", "TL": "Timor-Leste", "TN": "Tunisia", "TR": "Turkey",
    "UA": "Ukraine", "VA": "Vatican City", "VG": "British Virgin Islands",
    "XK": "Kosovo",
}


def _flag(iso: str) -> str:
    if not iso or len(iso) != 2 or not iso.isalpha():
        return "🏳️"
    return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in iso)


def normalize(raw: str) -> str:
    """Uppercase and drop every kind of whitespace."""
    return "".join((raw or "").upper().split())


def _mod97(iban: str) -> tuple[str, int]:
    """Rearranged IBAN → (alpha-expanded digits, remainder mod 97)."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rearranged  # A=10..Z=35
    )
    return digits, int(digits) % 97


async def lookup(q: str, cfg, bot=None) -> dict:
    norm = normalize(q)
    if not norm:
        raise ToolError(_HINT)
    if not _FORMAT_RE.match(norm):
        raise ToolError(
            "invalid IBAN format — letters and digits only, e.g. "
            "DE89370400440532013000"
        )

    iso = norm[:2]
    if iso not in _COUNTRY_LENGTHS:
        raise ToolError(f"unknown IBAN country code: {iso}")

    expected_len = _COUNTRY_LENGTHS[iso]
    length_ok = len(norm) == expected_len
    digits, remainder = _mod97(norm)
    checksum_ok = remainder == 1
    valid = length_ok and checksum_ok

    masked = (
        norm[:4] + "*" * max(len(norm) - 8, 0) + norm[-4:]
        if len(norm) >= 8
        else "*" * len(norm)
    )
    grouped = " ".join(norm[i:i + 4] for i in range(0, len(norm), 4))

    return {
        "iban": norm,
        "valid": valid,
        "country_code": iso,
        "country_name": _COUNTRY_NAMES.get(iso, iso),
        "flag": _flag(iso),
        "length": len(norm),
        "expected_length": expected_len,
        "length_ok": length_ok,
        "bank_code": norm[4:8],
        "checksum_ok": checksum_ok,
        "remainder": remainder,
        "digits": digits,
        "grouped": grouped,
        "masked": masked,
    }


def render(data: dict) -> str:
    d = data if isinstance(data, dict) else {}
    iso = str(d.get("country_code") or "?")
    flag = str(d.get("flag") or "🏳️")
    country = str(d.get("country_name") or iso)
    grouped = escape(str(d.get("grouped") or d.get("iban") or "?"))
    valid = bool(d.get("valid"))
    pill = "✅ valid" if valid else "❌ invalid"

    lines = [
        f"<blockquote>📇 {grouped} — {pill} IBAN · {flag} "
        f"{escape(country)}</blockquote>",
        "",
    ]

    lines.append(
        "<b>Validity:</b> ✅ Structurally valid IBAN"
        if valid
        else "<b>Validity:</b> ❌ Not a valid IBAN"
    )
    lines.append(f"<b>Country:</b> {flag} {escape(country)} (<code>{escape(iso)}</code>)")

    bank = d.get("bank_code")
    if bank:
        lines.append(f"<b>Bank code:</b> <code>{escape(str(bank))}</code>")

    length = d.get("length", 0)
    expected = d.get("expected_length", 0)
    length_ok = bool(d.get("length_ok"))
    lines.append(
        f"<b>Length:</b> {escape(str(length))} (expected {escape(str(expected))}) "
        f"{'✅' if length_ok else '❌'}"
    )

    remainder = d.get("remainder")
    checksum_ok = bool(d.get("checksum_ok"))
    lines.append(
        f"<b>Checksum:</b> mod-97 → <code>{escape(str(remainder))}</code> "
        f"{'✅ (expected 1)' if checksum_ok else '❌ (expected 1)'}"
    )

    lines.append("")
    lines.append(f"<b>Formatted:</b> <code>{grouped}</code>")
    masked = d.get("masked")
    if masked:
        lines.append(
            f"<b>Masked:</b> <span class=\"tg-spoiler\">"
            f"{escape(str(masked))}</span>"
        )
    digits = d.get("digits")
    if digits:
        lines.append(
            "<span class=\"tg-spoiler\">ℹ️ Checksum = rearranged IBAN with "
            "letters expanded (A=10…Z=35), then mod 97 must equal 1.</span>"
        )
    return "\n".join(lines)
