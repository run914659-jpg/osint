"""Single source of truth for the 16 OSINT tools.

Every consumer (main menu, tool handlers, osint modules, tests) imports from
here so codes/labels/prompts can never drift apart.

Module contract
---------------
Each osint module named in ``Tool.module`` MUST expose:

    async def lookup(q: str, cfg, bot=None) -> dict
        # raises osint.ToolError for bad user input / unavailable source
        # returns a plain dict consumed by render()

    def render(data: dict) -> str
        # returns HTML (parse_mode="HTML") — rich, emoji, <b>/<blockquote>/spoiler

``lookup()`` MAY additionally return an optional key:

    data["buttons"] = [Button, ...]   # see Button below, appended above the
                                      # standard [Again/Copy] + [Back] rows

Button tuple forms (built by callers via osintbot.keyboards.btn):
    (text, url)                      # url button, no style
    (text, callback_data)            # callback button
    (text, url_or_cb, style)         # style in {primary, success, danger}
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    code: str        # callback suffix: "t:<code>" (kept <= 64 bytes with prefix)
    emoji: str
    title: str       # menu label + result header
    prompt: str      # asked when the user taps the tool
    module: str      # osint.<module> providing lookup()/render()
    sample: str = ""  # example query shown in the prompt


TOOLS: tuple[Tool, ...] = (
    Tool("num", "📱", "Number \u2192 Info",
         "Send a phone number with country code:", "phone", "+919876543210"),
    Tool("mail", "📧", "Email \u2192 Info",
         "Send an email address:", "email", "example@gmail.com"),
    Tool("user", "👤", "Username \u2192 Sites",
         "Send a username to scan across ~18 sites:", "username", "john_doe"),
    Tool("tg", "🔍", "Telegram @ \u2192 Info",
         "Send a @username or t.me link:", "tguser", "@telegram"),
    Tool("tgid", "🆔", "Telegram ID \u2192 Info",
         "Send a numeric Telegram user ID:", "tgid", "777000"),
    Tool("ip", "🌐", "IP \u2192 Info",
         "Send an IPv4/IPv6 address:", "ipinfo", "8.8.8.8"),
    Tool("url", "🔗", "URL \u2192 Info",
         "Send a full URL (https://...):", "urlinfo", "https://example.com"),
    Tool("dns", "🌍", "Domain \u2192 DNS",
         "Send a domain:", "dnslookup", "google.com"),
    Tool("rdap", "🧬", "Domain \u2192 WHOIS",
         "Send a domain for WHOIS/RDAP:", "rdap", "google.com"),
    Tool("txt", "🔢", "Text \u2192 Hash / Encode",
         "Send any text to hash/encode:", "textutils", "hello world"),
    Tool("hash", "🔑", "Hash \u2192 Reverse",
         "Send an MD5 hash to try reversing:", "hashcrack",
         "5f4dcc3b5aa765d61d8327deb882cf99"),
    Tool("bin", "💳", "BIN \u2192 Card Info",
         "Send the first 6-8 digits of a card (BIN):", "binlookup", "45717300"),
    Tool("mac", "📡", "MAC \u2192 Vendor",
         "Send a MAC address:", "macvendor", "00:1A:2B:3C:4D:5E"),
    Tool("exif", "🖼️", "Image \u2192 EXIF",
         "Send an image URL (jpg/png/webp):", "exiftool",
         "https://example.com/photo.jpg"),
    Tool("iban", "📇", "IBAN \u2192 Validate",
         "Send an IBAN:", "iban", "DE89370400440532013000"),
    Tool("dork", "🔎", "Google Dork Builder",
         "Send a name/email/topic to build dorks:", "dork", "john@example.com"),
)

BY_CODE: dict[str, Tool] = {t.code: t for t in TOOLS}

# Sanity: callback "t:<code>" must fit Telegram's 64-byte limit with margin.
assert all(len(f"t:{t.code}".encode()) <= 64 for t in TOOLS)
