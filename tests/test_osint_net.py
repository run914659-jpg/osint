"""Phase 6 network-tool tests — OFFLINE by default (PLAN.md §6.2 / §5).

Nothing in the default run touches the network: pure helpers, fixture-driven
``render()``/parser calls and input-validation ``ToolError`` cases only.
Live checks exist but are gated behind ``LIVE=1``.
"""

from __future__ import annotations

import asyncio
import io
import os

import pytest
from PIL import Image

from osint import ToolError
from osint import binlookup, dnslookup, exiftool, hashcrack, ipinfo, macvendor, rdap, urlinfo, username
from osintbot.loaders import FRAMES, RAMP, LoadingAnimation

MD5_TEST = "098f6bcd4621d373cade4e832627b4f6"   # md5("test")


# ==========================================================================
# urlinfo — SSRF classification, title extraction, redirect chain
# ==========================================================================

@pytest.mark.parametrize(
    "addr",
    [
        "10.0.0.1",
        "127.0.0.1",
        "127.1.2.3",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",     # cloud metadata
        "0.0.0.0",
        "224.0.0.1",           # multicast
        "240.0.0.1",           # reserved
        "::1",                 # IPv6 loopback
        "fe80::1",             # IPv6 link-local
        "fd00::1",             # IPv6 ULA
        "::",                  # unspecified
        "::ffff:127.0.0.1",    # IPv4-mapped loopback
        "::ffff:10.0.0.1",     # IPv4-mapped private
    ],
)
def test_ssrf_blocked_addresses(addr):
    assert urlinfo._ip_blocked(addr) is True, f"{addr} must be blocked"


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_ssrf_public_addresses_allowed(addr):
    assert urlinfo._ip_blocked(addr) is False, f"{addr} must be allowed"


async def test_ssrf_assert_public_host_rejects_loopback():
    with pytest.raises(ToolError) as exc:
        await urlinfo._assert_public_host("localhost", 80)
    assert "private/local" in str(exc.value)


def test_url_normalize_adds_https_when_scheme_missing():
    url, host, port = urlinfo._normalize_url("example.org/path?a=1")
    assert url == "https://example.org/path?a=1"
    assert host == "example.org"
    assert port == 443


def test_url_normalize_keeps_explicit_scheme_and_port():
    url, host, port = urlinfo._normalize_url("http://example.org:8080/x")
    assert url == "http://example.org:8080/x"
    assert host == "example.org"
    assert port == 8080


@pytest.mark.parametrize("bad", ["", "   ", "no-dots-here", "ftp://example.org/a", "https://", "exa mple.org"])
def test_url_normalize_rejects_bad_input(bad):
    with pytest.raises(ToolError):
        urlinfo._normalize_url(bad)


def test_title_extraction_from_sample_html():
    html_doc = (
        "<html><head><title>  Hello &amp;\n   World  </title></head>"
        "<body>noise <title>ignored second</title></body></html>"
    )
    assert urlinfo._extract_title(html_doc) == "Hello & World"


def test_title_extraction_missing_title():
    assert urlinfo._extract_title("<html><body>no title here</body></html>") is None


def test_redirect_chain_formatting():
    chain = [
        {"url": "http://a.example", "status": 301},
        {"url": "https://b.example/x", "status": 302},
        {"url": "https://b.example/final", "status": 200},
    ]
    assert urlinfo._format_chain(chain) == (
        "http://a.example (301) → https://b.example/x (302) → https://b.example/final (200)"
    )


def test_urlinfo_render_is_defensive():
    html_out = urlinfo.render({"chain": [{"url": "https://x.test/", "status": 200}]})
    assert "No response" in html_out
    assert "not available" in html_out or "not sent" in html_out

    full = urlinfo.render(
        {
            "status": 200,
            "reason": "OK",
            "final_url": "https://example.org/",
            "title": "Example & Test",
            "server": "nginx",
            "content_type": "text/html; charset=utf-8",
            "ip": "93.184.216.34",
            "chain": [{"url": "https://example.org/", "status": 200}],
            "body_bytes": 1256,
            "truncated": False,
        }
    )
    assert "<b>200</b> OK" in full
    assert "Example &amp; Test" in full       # HTML-escaped
    assert "<code>93.184.216.34</code>" in full


# ==========================================================================
# hashcrack — validation + gromweb HTML parsing
# ==========================================================================

@pytest.mark.parametrize("bad", ["x" * 33, "0" * 31, "098f6bcd4621d373cade4e832627b4f6z",
                                "not-a-hash", "5f4dcc3b5aa765d61d8327deb882cf9"])  # 31
def test_hashcrack_rejects_non_md5(bad):
    with pytest.raises(ToolError):
        hashcrack._validate(bad)


def test_hashcrack_accepts_md5_and_lowercases():
    assert hashcrack._validate("  098F6BCD4621D373CADE4E832627B4F6  ") == MD5_TEST


# Real gromweb markup (fetched 2026-10): no id="hash_string" element — the
# result lives in <p class="word-break-all"> … <a class="String">.
GROMWEB_FOUND = """
<html><head><title>MD5 reverse for 098f6bcd4621d373cade4e832627b4f6</title></head>
<body><div id="content" class="Content">
<p class="word-break-all">The MD5 hash <a class="Hash" href="/?md5=098f6bcd4621d373cade4e832627b4f6">098f6bcd4621d373cade4e832627b4f6</a> was successfully reversed into the string <a class="String" href="/?string=test">test</a></p>
</div><p>&copy; GromWeb 2007-2026</p><p>Known MD5 sums: 832,927,522</p></body></html>
"""

GROMWEB_NOT_FOUND = """
<html><head><title>MD5 conversion and MD5 reverse lookup</title></head>
<body><div id="content" class="Content">
<p>Provided MD5 hash could not be reversed into a string: no reverse string was found.</p>
<p>For instance, the MD5 hash of the word <a class="String" href="/?string=password">password</a>
is <a class="Hash" href="/?md5=5f4dcc3b5aa765d61d8327deb882cf99">5f4dcc3b5aa765d61d8327deb882cf99</a>.</p>
</div><p>Known MD5 sums: 832,927,522</p></body></html>
"""

GROMWEB_HASH_STRING_VARIANT = '<span id="hash_string">hunter2</span>'


def test_hashcrack_parses_found_page():
    found, plaintext, known = hashcrack._parse_page(GROMWEB_FOUND, MD5_TEST)
    assert found is True
    assert plaintext == "test"
    assert known == "832,927,522"


def test_hashcrack_parses_not_found_page_without_false_positive():
    # the explanation section links class="String" examples — must NOT count
    found, plaintext, known = hashcrack._parse_page(GROMWEB_NOT_FOUND, MD5_TEST)
    assert found is False
    assert plaintext is None
    assert known == "832,927,522"


def test_hashcrack_parses_hash_string_variant():
    found, plaintext, _ = hashcrack._parse_page(GROMWEB_HASH_STRING_VARIANT, "a" * 32)
    assert found is True and plaintext == "hunter2"


def test_hashcrack_render_found_uses_spoiler():
    out = hashcrack.render(
        {"hash": MD5_TEST, "found": True, "plaintext": "test", "known_sums": "832,927,522"}
    )
    assert "tg-spoiler" in out
    assert "<code>test</code>" in out
    assert "Reversed" in out


def test_hashcrack_render_not_found_is_honest():
    out = hashcrack.render(
        {"hash": MD5_TEST, "found": False, "plaintext": None, "known_sums": "832,927,522"}
    )
    assert "Not reversed" in out
    assert "832,927,522" in out
    assert "tg-spoiler" not in out


# ==========================================================================
# binlookup — validation + graceful render
# ==========================================================================

def test_binlookup_accepts_6_to_8_digits():
    assert binlookup._validate("45717300") == "45717300"
    assert binlookup._validate("457173") == "457173"
    assert binlookup._validate(" 4571 7300 ") == "45717300"


def test_binlookup_refuses_full_card_number():
    with pytest.raises(ToolError) as exc:
        binlookup._validate("4571730012345678")     # 16 digits
    assert "not the full number" in str(exc.value)


def test_binlookup_refuses_too_short_and_non_digit():
    with pytest.raises(ToolError):
        binlookup._validate("45717")                # 5 digits
    with pytest.raises(ToolError):
        binlookup._validate("4571730a")             # letter
    with pytest.raises(ToolError):
        binlookup._validate("")
    with pytest.raises(ToolError):
        binlookup._validate("4571 730a")


BIN_FIXTURE = {
    "bin": "45717300",
    "scheme": "visa",
    "type": "debit",
    "brand": "Visa Classic/Dankort",
    "name": "Dankort",
    "bank": {"name": "Djurslands Bank A/S", "city": None, "url": None, "phone": None},
    "country": {"name": "Denmark", "emoji": "\U0001F1E9\U0001F1F0", "currency": "DKK", "alpha2": "DK"},
}


def test_binlookup_render_full_fixture():
    out = binlookup.render(BIN_FIXTURE)
    assert "Djurslands Bank A/S" in out
    assert "Denmark" in out
    assert "\U0001F1E9\U0001F1F0" in out
    assert "45717300" in out


def test_binlookup_render_handles_missing_bank_and_country():
    out = binlookup.render({"bin": "457173", "scheme": "visa", "type": "credit",
                            "brand": None, "name": None, "bank": None, "country": None})
    assert "Bank:</b> <i>not available</i>" in out
    assert "Country:</b> <i>not available</i>" in out

    partial = binlookup.render(dict(BIN_FIXTURE, bank={"name": None, "city": "Aarhus"},
                                    country=None))
    assert "Aarhus" in partial
    assert "Country:</b> <i>not available</i>" in partial


# ==========================================================================
# username — validation, classification, render ordering
# ==========================================================================

@pytest.mark.parametrize("ok", ["john_doe", "torvalds", "a.b-c_1", "ab", "@alice", "  bob  "])
def test_username_validation_accepts(ok):
    expected = ok.strip().lstrip("@")
    assert username._validate_username(ok) == expected


@pytest.mark.parametrize("bad", ["x", "a" * 33, "has space", "evil/../x", "ünicode", ""])
def test_username_validation_rejects(bad):
    with pytest.raises(ToolError):
        username._validate_username(bad)


@pytest.mark.parametrize(
    "code,state,symbol",
    [
        (404, "not_found", "❌"),
        (200, "found", "✅"),
        (403, "unknown", "⚠️"),
        (401, "unknown", "⚠️"),
        (429, "unknown", "⚠️"),
        (500, "unknown", "⚠️"),
        (None, "unknown", "⚠️"),
    ],
)
def test_username_classification(code, state, symbol):
    assert username._classify(code) == state
    assert username._EMOJI[username._classify(code)] == symbol


def test_username_registry_shape():
    assert len(username.REGISTRY) == 18
    assert all("{u}" in tpl for tpl in username.REGISTRY.values())
    assert all(tpl.startswith("https://") for tpl in username.REGISTRY.values())


def test_username_render_orders_found_first():
    data = {
        "username": "torvalds",
        "results": [
            {"site": "Nope", "url": "https://nope/x", "code": 404, "state": "not_found", "note": None},
            {"site": "GitHub", "url": "https://github.com/torvalds", "code": 200, "state": "found", "note": None},
            {"site": "Insta", "url": "https://instagram/x", "code": 429, "state": "unknown", "note": None},
        ],
        "found": 1, "unknown": 1, "not_found": 1, "total": 3,
    }
    out = username.render(data)
    assert out.index("GitHub") < out.index("Insta") < out.index("Nope")
    assert "✅ 1 · ⚠️ 1 · ❌ 1" in out
    assert '<a href="https://github.com/torvalds">github.com/torvalds</a>' in out
    assert "does not prove the username is free" in out


# ==========================================================================
# rdap / ipinfo / dns / mac / exif — input validation + render from fixtures
# ==========================================================================

RDAP_FIXTURE = {
    "ldhName": "google.com",
    "status": ["client transfer prohibited"],
    "entities": [
        {"roles": ["registrar"], "handle": "292",
         "vcardArray": ["vcard", [
             ["version", {}, "text", "4.0"],
             ["fn", {}, "text", "MarkMonitor Inc."],
             ["email", {}, "text", "ccops@markmonitor.com"],
         ]]},
        {"roles": ["registrant"]},
    ],
    "events": [
        {"eventAction": "registration", "eventDate": "1997-09-15T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2028-09-14T04:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2019-09-09T15:39:04Z"},
        {"eventAction": "last update of RDAP database", "eventDate": "2026-10-21T16:50:05Z"},
    ],
    "nameservers": [{"ldhName": "NS1.GOOGLE.COM"}, {"ldhName": "ns2.google.com."}],
}


def test_rdap_extract_from_payload():
    data = rdap._extract(RDAP_FIXTURE)
    assert data["domain"] == "google.com"
    assert data["registrar"] == "MarkMonitor Inc."
    assert data["statuses"] == ["client transfer prohibited"]
    assert data["events"]["registration"] == "1997-09-15"
    assert data["events"]["expiration"] == "2028-09-14"
    assert data["nameservers"] == ["ns1.google.com", "ns2.google.com"]


def test_rdap_extract_defensive_on_empty_payload():
    data = rdap._extract({})
    assert data["registrar"] is None
    assert data["events"] == {}
    assert data["nameservers"] == []
    assert data["statuses"] == []


@pytest.mark.parametrize("bad", ["", "nodot", "https://", "exa mple.com"])
def test_rdap_domain_validation(bad):
    with pytest.raises(ToolError):
        rdap._normalize_domain(bad)


def test_rdap_render_formats_dates_and_handles_missing_fields():
    out = rdap.render(rdap._extract(RDAP_FIXTURE))
    assert "MarkMonitor Inc." in out
    assert "<code>1997-09-15</code>" in out
    assert "ns1.google.com" in out
    assert "client transfer prohibited" in out

    empty = rdap.render(rdap._extract({}))
    assert "not available" in empty


IP_API_FIXTURE = {
    "status": "success", "country": "United States", "regionName": "California",
    "city": "Mountain View", "zip": "94043", "lat": 37.423, "lon": -122.083,
    "isp": "Google LLC", "org": "Google LLC", "as": "AS15169 Google LLC",
    "query": "8.8.8.8", "mobile": False, "proxy": False, "hosting": True,
}


def test_ipinfo_validates_input():
    assert ipinfo._validate_ip(" 8.8.8.8 ") == "8.8.8.8"
    assert ipinfo._validate_ip("2001:4860:4860::8888") == "2001:4860:4860::8888"
    for bad in ("", "999.1.1.1", "example.com", "8.8.8", "::zz"):
        with pytest.raises(ToolError):
            ipinfo._validate_ip(bad)


def test_ipinfo_api_failure_raises_tool_error():
    with pytest.raises(ToolError) as exc:
        ipinfo._from_ip_api({"status": "fail", "message": "private range"})
    assert "private range" in str(exc.value)


def test_ipinfo_render_from_fixture():
    out = ipinfo.render(ipinfo._from_ip_api(IP_API_FIXTURE))
    assert "Mountain View" in out
    assert "<code>8.8.8.8</code>" in out
    assert "Google LLC" in out
    assert "AS15169" in out
    assert "Hosting/DC: ✔️" in out
    assert "Mobile: ✖️" in out
    assert "Source: ip-api.com" in out


def test_ipinfo_render_handles_missing_fields():
    out = ipinfo.render({"ip": "1.2.3.4"})
    assert "not available" in out
    assert "Mobile: —" in out
    assert "Unknown country" in out


DNS_FIXTURE = {
    "domain": "example.com",
    "records": {
        "A": {"ok": True, "values": ["93.184.216.34"], "ttl": 300, "note": None},
        "AAAA": {"ok": False, "values": [], "ttl": None, "note": "no records"},
        "MX": {"ok": True, "values": ["10 mail.example.com"], "ttl": 100, "note": None},
        "NS": {"ok": True, "values": ["a.iana-servers.net"], "ttl": 86400, "note": None},
        "TXT": {"ok": True, "values": ["v=spf1 -all"], "ttl": 300, "note": None},
        "CNAME": {"ok": False, "values": [], "ttl": None, "note": "timed out"},
    },
}


def test_dns_normalize_domain():
    assert dnslookup._normalize_domain("https://www.Example.com/path?q=1") == "www.example.com"
    assert dnslookup._normalize_domain("EXAMPLE.COM.") == "example.com"
    assert dnslookup._normalize_domain("example.com:443") == "example.com"
    assert dnslookup._normalize_domain("user@example.com") == "example.com"
    for bad in ("", "nodot", "http://", "example .com"):
        with pytest.raises(ToolError):
            dnslookup._normalize_domain(bad)


def test_dns_render_marks_missing_records():
    out = dnslookup.render(DNS_FIXTURE)
    assert "<b>A:</b>" in out
    assert "<code>93.184.216.34</code>" in out
    assert "TTL 300s" in out
    assert "AAAA:</b> —" in out
    assert "timed out" in out
    assert "4 record(s) found" in out   # 4 usable records across 6 queried types


def test_dns_render_is_defensive():
    out = dnslookup.render({"domain": "x.io", "records": {}})
    assert "x.io" in out
    assert "A:</b> —" in out            # every type degrades to an em dash


@pytest.mark.parametrize("bad", ["00:1A:2B:3C:4D:5G", "00-1A-2B-3C-4D-5", "12345", "", "00:1A:2B:3C:4D:5E:6F"])
def test_mac_rejects_invalid(bad):
    with pytest.raises(ToolError):
        macvendor._normalize(bad)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("00:1A:2B:3C:4D:5E", "00:1A:2B:3C:4D:5E"),
        ("00-1a-2b-3c-4d-5e", "00:1A:2B:3C:4D:5E"),
        ("001a2b3c4d5e", "00:1A:2B:3C:4D:5E"),
    ],
)
def test_mac_normalizes(raw, expected):
    assert macvendor._normalize(raw) == expected


def test_mac_render_from_fixture():
    out = macvendor.render({"mac": "00:1A:2B:3C:4D:5E", "oui": "00:1A:2B", "vendor": "Acme Devices"})
    assert "Acme Devices" in out
    assert "<code>00:1A:2B</code>" in out


def test_exif_input_must_be_url():
    for bad in ("", "not a url", "photo.jpg", "ftp://x.org/a.jpg", "https://"):
        with pytest.raises(ToolError):
            exiftool._validate_input(bad)
    assert exiftool._validate_input(" https://x.org/a.jpg ") == "https://x.org/a.jpg"


def test_exif_magic_byte_detection():
    jpeg = exiftool._looks_like_image(b"\xff\xd8\xff\xe0" + b"\x00" * 12, "application/octet-stream")
    png = exiftool._looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "")
    webp = exiftool._looks_like_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "")
    gif = exiftool._looks_like_image(b"GIF89a" + b"\x00" * 10, "")
    by_ctype = exiftool._looks_like_image(b"<!DOCTYPE html><html>", "image/webp; charset=binary")
    html_body = exiftool._looks_like_image(b"<!DOCTYPE html><html>", "text/html")

    assert (jpeg, png, webp, gif) == ("JPEG", "PNG", "WEBP", "GIF")
    assert by_ctype == "webp"
    assert html_body is None


def test_exif_probe_reports_no_metadata_for_clean_image():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="JPEG")
    info = exiftool._probe(buf.getvalue())
    assert info["format"] == "JPEG"
    assert (info["width"], info["height"]) == (4, 4)
    assert info["has_exif"] is False
    assert info["gps"] is False
    assert info["fields"] == {}


def test_exif_probe_extracts_datetime_make_model():
    buf = io.BytesIO()
    img = Image.new("RGB", (8, 6), "blue")
    exif = Image.Exif()
    exif[306] = "2024:01:02 03:04:05"
    exif[271] = "TestCam"
    exif[272] = "Model X"
    exif[305] = "UnitTest 1.0"
    img.save(buf, format="JPEG", exif=exif)

    info = exiftool._probe(buf.getvalue())
    assert info["has_exif"] is True
    assert info["fields"]["DateTime"] == "2024:01:02 03:04:05"
    assert info["fields"]["Make"] == "TestCam"
    assert info["fields"]["Model"] == "Model X"
    assert info["gps"] is False


def test_exif_render_without_metadata_is_honest():
    out = exiftool.render(
        {"url": "https://x.org/a.jpg", "format": "JPEG", "width": 10, "height": 5,
         "bytes": 1024, "fields": {}, "gps": False, "has_exif": False}
    )
    assert "No EXIF metadata" in out
    assert "cleaned/re-saved" in out
    assert "GPS block:</b> not present" in out


def test_exif_render_with_metadata():
    out = exiftool.render(
        {"url": "https://x.org/b.jpg", "format": "JPEG", "width": 10, "height": 5,
         "bytes": 2048, "fields": {"DateTime": "2024:01:02 03:04:05", "Make": "Acme"},
         "gps": True, "gps_version": "2.3.0.0", "has_exif": True}
    )
    assert "DateTime" in out
    assert "Acme" in out
    assert "GPS block:</b> present" in out
    assert "No EXIF metadata" not in out


# ==========================================================================
# loaders — animation behaviour (fake bot, no Telegram)
# ==========================================================================

class _FakeBot:
    def __init__(self, fail: str | None = None):
        self.edits: list[str] = []
        self.fail = fail

    async def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None):
        self.edits.append(text)
        if self.fail == "not modified":
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=None, message="Bad Request: message is not modified")
        if self.fail == "other":
            raise RuntimeError("network is down")


def test_loader_frame_shape_and_percent():
    anim = LoadingAnimation(_FakeBot(), 1, 2, "🔍 <b>Searching</b>...", query="google.com")
    first = anim._frame(0.0, 0)
    mid = anim._frame(RAMP / 2, 4)
    last = anim._frame(RAMP, 8)

    assert first.startswith(FRAMES[0])
    assert "<b>0%</b>" in first
    assert "<b>50%</b>" in mid
    assert "<b>99%</b>" in last
    assert "<blockquote>google.com</blockquote>" in first
    # frame cycles so consecutive edits never produce identical text
    assert anim._frame(0.0, 1).startswith(FRAMES[1])


def test_loader_escapes_query_html():
    anim = LoadingAnimation(_FakeBot(), 1, 2, "cap", query="<b>&evil")
    assert "&lt;b&gt;&amp;evil" in anim._frame(0.0, 0)


async def test_loader_start_stop_and_no_partial():
    bot = _FakeBot()
    anim = LoadingAnimation(bot, 1, 2, "🔍 <b>Searching</b>...", query="q")
    await anim.start()
    await anim.start()          # idempotent
    await asyncio.sleep(0.05)   # let the first frame land
    partial = await anim.stop()

    assert partial is False
    assert len(bot.edits) >= 1
    assert bot.edits[0].startswith(FRAMES[0])
    assert await anim.stop() is False   # second stop() is safe


async def test_loader_swallows_telegram_errors():
    bot = _FakeBot(fail="not modified")
    anim = LoadingAnimation(bot, 1, 2, "cap")
    await anim.start()
    await anim.stop()           # must not raise
    assert anim.partial is False

    bot = _FakeBot(fail="other")
    anim = LoadingAnimation(bot, 1, 2, "cap")
    await anim.start()
    await anim.stop()           # must not raise


# ==========================================================================
# live checks — gated: run with  LIVE=1  pytest -q tests/test_osint_net.py
# ==========================================================================

@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="live (LIVE=1)")
async def test_live_ipinfo():
    data = await ipinfo.lookup("8.8.8.8", cfg=None)
    assert data.get("ip") == "8.8.8.8"
    assert data.get("country")
    assert ipinfo.render(data)


@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="live (LIVE=1)")
async def test_live_hashcrack_reverses_test():
    data = await hashcrack.lookup(MD5_TEST, cfg=None)
    assert data["found"] is True
    assert data["plaintext"] == "test"


@pytest.mark.skipif(os.getenv("LIVE") != "1", reason="live (LIVE=1)")
async def test_live_urlinfo_and_rdap():
    url_data = await urlinfo.lookup("https://example.org", cfg=None)
    assert url_data["status"] == 200
    assert url_data["title"]

    rdap_data = await rdap.lookup("google.com", cfg=None)
    assert rdap_data["registrar"]
