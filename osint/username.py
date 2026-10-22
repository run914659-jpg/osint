"""Username → existence across ~18 sites (Phase 6, PLAN.md §6.2).

One ``aiohttp`` session, a ``TCPConnector(limit=15)`` and one GET per site
(``allow_redirects=True``, 6 s per request, hard 8 s overall cap):

* ``404`` → ❌ profile does not exist
* ``200`` → ✅ profile exists
* ``401/403/429`` (or any other answer, or an exception) → ⚠️ unknown —
  several big sites block datacenter IPs, so "no answer" is *not* "no user"
"""

from __future__ import annotations

import asyncio
import html
import re

import aiohttp

from osint import ToolError

__all__ = ["lookup", "render", "REGISTRY", "USERNAME_RE"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PER_SITE_TIMEOUT = 6
OVERALL_TIMEOUT = 8
CONNECTOR_LIMIT = 15

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")

REGISTRY: dict[str, str] = {
    "GitHub": "https://github.com/{u}",
    "GitLab": "https://gitlab.com/{u}",
    "Reddit": "https://www.reddit.com/user/{u}",
    "Instagram": "https://www.instagram.com/{u}/",
    "TikTok": "https://www.tiktok.com/@{u}",
    "X (Twitter)": "https://x.com/{u}",
    "YouTube": "https://www.youtube.com/@{u}",
    "Pinterest": "https://www.pinterest.com/{u}/",
    "Tumblr": "https://{u}.tumblr.com",
    "Medium": "https://medium.com/@{u}",
    "Dev.to": "https://dev.to/{u}",
    "Steam": "https://steamcommunity.com/id/{u}",
    "Twitch": "https://www.twitch.tv/{u}",
    "Spotify": "https://open.spotify.com/user/{u}",
    "SoundCloud": "https://soundcloud.com/{u}",
    "About.me": "https://about.me/{u}",
    "Linktree": "https://linktr.ee/{u}",
    "Keybase": "https://keybase.io/{u}",
}

# state ranks: found first, then unknown, then definitely-not-found
_RANK = {"found": 0, "unknown": 1, "not_found": 2}
_EMOJI = {"found": "✅", "unknown": "⚠️", "not_found": "❌"}


def _validate_username(q: str) -> str:
    raw = (q or "").strip().lstrip("@")
    if not USERNAME_RE.match(raw):
        raise ToolError(
            f"<code>{html.escape(raw[:40])}</code> is not a valid username.\n"
            "Use 2-32 letters, digits, dot, underscore or dash (no spaces)."
        )
    return raw


def _classify(code: int | None) -> str:
    """HTTP status → state. Pure helper, unit-tested offline."""
    if code == 404:
        return "not_found"
    if code == 200:
        return "found"
    return "unknown"          # 401/403/429/5xx/odd codes → can't tell


async def _check(session: aiohttp.ClientSession, site: str, template: str, user: str) -> dict:
    url = template.format(u=user)
    entry = {"site": site, "url": url, "code": None, "state": "unknown", "note": None}
    try:
        resp = await asyncio.wait_for(
            session.get(url, allow_redirects=True), PER_SITE_TIMEOUT
        )
        async with resp:
            entry["code"] = resp.status
            entry["state"] = _classify(resp.status)
            # drain a sliver of the body so the connection can be reused
            await asyncio.wait_for(resp.content.read(1024), PER_SITE_TIMEOUT)
    except asyncio.TimeoutError:
        entry["note"] = "timeout"
    except asyncio.CancelledError:
        raise
    except (aiohttp.ClientError, OSError):
        entry["note"] = "network error"
    except Exception as exc:                      # pragma: no cover - defensive
        entry["note"] = type(exc).__name__
    return entry


# --------------------------------------------------------------------------
# module contract
# --------------------------------------------------------------------------

async def lookup(q: str, cfg, bot=None) -> dict:
    user = _validate_username(q)

    connector = aiohttp.TCPConnector(limit=CONNECTOR_LIMIT)
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=PER_SITE_TIMEOUT),
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
    )
    try:
        tasks = [
            asyncio.create_task(_check(session, site, template, user))
            for site, template in REGISTRY.items()
        ]
        # asyncio.wait (instead of a bare gather) so one slow site yields a
        # partial scan instead of failing the whole tool after 8 s.
        done, pending = await asyncio.wait(tasks, timeout=OVERALL_TIMEOUT)
        results: list[dict] = []
        for task, (site, template) in zip(tasks, REGISTRY.items()):
            if task in done:
                try:
                    results.append(task.result())
                except Exception as exc:          # pragma: no cover - defensive
                    results.append(
                        {"site": site, "url": template.format(u=user), "code": None,
                         "state": "unknown", "note": type(exc).__name__}
                    )
            else:
                task.cancel()
                results.append(
                    {"site": site, "url": template.format(u=user), "code": None,
                     "state": "unknown", "note": "timeout"}
                )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await session.close()

    results.sort(key=lambda r: (_RANK.get(r["state"], 1),))
    found = sum(1 for r in results if r["state"] == "found")
    unknown = sum(1 for r in results if r["state"] == "unknown")
    not_found = sum(1 for r in results if r["state"] == "not_found")

    return {
        "username": user,
        "results": results,
        "found": found,
        "unknown": unknown,
        "not_found": not_found,
        "total": len(results),
    }


def render(data: dict) -> str:
    user = data.get("username") or "—"
    results = data.get("results") or []
    found = data.get("found", 0)
    unknown = data.get("unknown", 0)
    not_found = data.get("not_found", 0)

    head = (
        f"👤 <b>@{html.escape(str(user))}</b> — "
        f"✅ {found} · ⚠️ {unknown} · ❌ {not_found}"
    )
    lines = [f"<blockquote>{head}</blockquote>"]

    # already ordered found → unknown → not_found by lookup(), sort again
    # defensively in case render() gets a hand-built dict
    ordered = sorted(results, key=lambda r: _RANK.get(r.get("state"), 1))
    for entry in ordered:
        site = html.escape(str(entry.get("site") or "?"))
        state = entry.get("state") or "unknown"
        emoji = _EMOJI.get(state, "⚠️")
        if state == "found":
            url = str(entry.get("url") or "")
            label = url.replace("https://", "").replace("http://", "").rstrip("/")
            lines.append(f'{emoji} <b>{site}</b> — <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
        elif state == "unknown":
            note = entry.get("note")
            detail = f" ({html.escape(str(note))})" if note else " — blocked/private, can't confirm"
            lines.append(f"{emoji} <b>{site}</b>{detail}")
        else:
            lines.append(f"{emoji} <b>{site}</b> — no profile")

    lines.append(
        "\nℹ️ ⚠️ means the site refused or rate-limited the check — "
        "it does not prove the username is free."
    )
    return "\n".join(lines)
