"""Offline-by-default unit tests for the locally owned OSINT modules.

Live network checks are gated behind ``LIVE=1``.
"""

from __future__ import annotations

import os
import urllib.parse
from types import SimpleNamespace

import pytest

from osint import ToolError
from osint import dork, email, iban, phone, tgid, textutils, tguser

CFG = SimpleNamespace(numverify_api_key=None)

# md5("test@example.com") — precomputed literal (NOT circular hashlib output).
GRAVATAR_MD5_TEST = "55502f40dc8b7c769880b10874abc9d0"


# --- phone -----------------------------------------------------------------

async def test_phone_india_lookup():
    data = await phone.lookup("+919876543210", CFG)
    assert data["valid"] is True
    assert data["country_code"] == "IN"
    assert data["country_name"] == "India"
    assert data["e164"] == "+919876543210"
    assert data["numverify"] is None  # no key → enrichment omitted
    out = phone.render(data)
    assert "✅ valid" in out
    assert "India" in out


async def test_phone_garbage_raises():
    with pytest.raises(ToolError):
        await phone.lookup("hello world", CFG)


async def test_phone_missing_country_code_raises():
    with pytest.raises(ToolError):
        await phone.lookup("9876543210", CFG)


async def test_phone_blank_raises():
    with pytest.raises(ToolError):
        await phone.lookup("   ", CFG)


# --- textutils -------------------------------------------------------------

async def test_textutils_md5_test():
    data = await textutils.lookup("test", CFG)
    assert data["md5"] == "098f6bcd4621d373cade4e832627b4f6"


async def test_textutils_sha256_abc():
    data = await textutils.lookup("abc", CFG)
    assert data["sha256"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


async def test_textutils_base64_roundtrip():
    encoded = await textutils.lookup("hello world", CFG)
    assert encoded["b64"] == "aGVsbG8gd29ybGQ="
    decoded = await textutils.lookup("aGVsbG8gd29ybGQ=", CFG)
    assert decoded["decode"] == "hello world"
    assert decoded["decode_error"] is None


async def test_textutils_reverse_and_counts():
    data = await textutils.lookup("abc", CFG)
    assert data["reversed"] == "cba"
    assert data["chars"] == 3
    assert data["words"] == 1
    assert data["lines"] == 1


async def test_textutils_bad_b64_still_shows_hashes():
    data = await textutils.lookup("not@valid@@b64!!", CFG)
    assert data["decode"] is None
    assert "Base64" in (data["decode_error"] or "")
    assert data["md5"]  # hashes/encodings are never lost to a bad decode


def test_textutils_b64decode_helper_raises():
    with pytest.raises(ToolError):
        textutils.b64decode("!!! not base64 !!!")


async def test_textutils_blank_raises():
    with pytest.raises(ToolError):
        await textutils.lookup("   ", CFG)


# --- iban ------------------------------------------------------------------

async def test_iban_valid_german_with_spaces():
    data = await iban.lookup("DE89 3704 0044 0532 0130 00", CFG)
    assert data["valid"] is True
    assert data["country_code"] == "DE"
    assert data["bank_code"] == "3704"
    assert data["remainder"] == 1
    assert "✅ valid" in iban.render(data)


async def test_iban_bad_checksum_renders_invalid():
    data = await iban.lookup("DE00370400440532013000", CFG)
    assert data["valid"] is False
    assert data["checksum_ok"] is False
    assert "❌ invalid" in iban.render(data)


async def test_iban_unknown_country_raises():
    with pytest.raises(ToolError):
        await iban.lookup("XX89370400440532013000", CFG)


async def test_iban_garbage_raises():
    with pytest.raises(ToolError):
        await iban.lookup("not-an-iban", CFG)


# --- email -----------------------------------------------------------------

def test_email_regex_cases():
    assert email.is_valid("john@example.com")
    assert email.is_valid("john.doe+tag@sub.example.co.uk")
    assert not email.is_valid("no-at-sign")
    assert not email.is_valid("two@@example.com")
    assert not email.is_valid("john@nodot")      # no dot in domain
    assert not email.is_valid("john@example.c")  # 1-char TLD


async def test_email_lookup_rejects_bad_format_offline():
    for bad in ("no-at-sign", "two@@example.com", "john@nodot"):
        with pytest.raises(ToolError):
            await email.lookup(bad, CFG)


def test_disposable_detection():
    assert "mailinator.com" in email.DISPOSABLE_DOMAINS
    assert len(email.DISPOSABLE_DOMAINS) >= 40
    assert email.is_disposable("mailinator.com")
    assert email.is_disposable("mx.mailinator.com")  # subdomain too
    assert not email.is_disposable("example.com")


def test_gravatar_hash_precomputed_literal():
    assert email.gravatar_hash("test@example.com") == GRAVATAR_MD5_TEST
    assert email.gravatar_hash("Test@Example.COM") == GRAVATAR_MD5_TEST
    assert len(email.gravatar_hash("anything@example.com")) == 32


# --- dork ------------------------------------------------------------------

async def test_dork_basic_queries_and_urls():
    data = await dork.lookup("john doe", CFG)
    assert 1 <= len(data["buttons"]) <= 6
    for label, url in data["queries"]:
        assert url.startswith("https://www.google.com/search?q=")
    # first query is the exact-phrase dork; its URL carries the encoded input
    assert urllib.parse.quote_plus('"john doe"') in data["queries"][0][1]


async def test_dork_quoted_input_keeps_quotes_balanced():
    data = await dork.lookup('he said "hi" loudly', CFG)
    assert len(data["buttons"]) <= 6
    for _label, query in data["dorks"]:
        assert query.count('"') % 2 == 0


async def test_dork_email_variant_prioritizes_extras():
    data = await dork.lookup("john@example.com", CFG)
    labels = [label for label, _query in data["dorks"]]
    assert "Pastebin dumps" in labels
    assert "Spreadsheets" in labels
    assert len(data["buttons"]) == 6


async def test_dork_input_bounds():
    with pytest.raises(ToolError):
        await dork.lookup("ab", CFG)
    with pytest.raises(ToolError):
        await dork.lookup("x" * 101, CFG)


def test_dork_render_mentions_legal_note():
    data = {"query": "john", "dorks": [("Exact phrase", '"john"')]}
    out = dork.render(data)
    assert "PUBLIC" in out
    assert "tg-spoiler" in out


# --- tguser (pure helpers only — no network) -------------------------------

def test_extract_username_normalization():
    assert tguser.extract_username("https://t.me/foo") == "foo"
    assert tguser.extract_username("t.me/bar99") == "bar99"
    assert tguser.extract_username("@baz_qux") == "baz_qux"
    assert tguser.extract_username("plainname") == "plainname"
    assert tguser.extract_username("https://t.me/s/channelx") == "channelx"
    assert tguser.extract_username("https://t.me/foo?start=x") == "foo"


def test_extract_username_rejects_invites_and_other_sites():
    for bad in ("https://t.me/+AbCdEfGh", "https://t.me/joinchat/AAAAAE",
                "t.me/+xyz123", "https://example.com/foo"):
        with pytest.raises(ToolError):
            tguser.extract_username(bad)


async def test_tguser_validation_runs_before_any_network():
    with pytest.raises(ToolError):  # too short for Telegram's 5-char minimum
        await tguser.lookup("ab", CFG)
    with pytest.raises(ToolError):  # valid name, but no bot available offline
        await tguser.lookup("validname", CFG)


# --- tgid (pure helpers + render only — no network) ------------------------

def test_parse_user_id_normalization():
    assert tgid.parse_user_id("777000") == 777000
    assert tgid.parse_user_id(" 1234567890 ") == 1234567890


def test_parse_user_id_rejects_non_positive_int():
    for bad in ("abc", "12.5", "-5", "0", "", "12 34", "1234567890123456"):
        with pytest.raises(ToolError):
            tgid.parse_user_id(bad)


def test_tgid_render_never_shows_phone_numbers():
    not_found = tgid.render({"found": False, "id": 123})
    assert "phone numbers" in not_found
    assert "tg-spoiler" in not_found
    found = tgid.render(
        {"found": True, "id": 777000, "first_name": "Test", "is_premium": True}
    )
    assert "<code>777000</code>" in found
    assert "phone numbers" in found


# --- live network checks (opt-in) ------------------------------------------

@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="set LIVE=1 for network tests")
async def test_live_email_mx_gravatar_smtp():
    data = await email.lookup("example@gmail.com", CFG)
    assert data["mx"], "gmail.com must publish MX records"
    assert len(data["gravatar_hash"]) == 32
    assert data["smtp"] in {"reachable", "blocked"}


@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="set LIVE=1 for network tests")
async def test_live_phone_numverify_optional():
    key = os.getenv("NUMVERIFY_API_KEY")
    if not key:
        pytest.skip("NUMVERIFY_API_KEY not set")
    cfg = SimpleNamespace(numverify_api_key=key)
    data = await phone.lookup("+14155552671", cfg)
    assert "numverify" in data
