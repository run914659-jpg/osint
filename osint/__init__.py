"""Pure OSINT lookup package.

Contract for every module (see osintbot/toolspec.py):

    async def lookup(q: str, cfg, bot=None) -> dict
    def render(data: dict) -> str   # HTML

This package never imports aiogram handlers; ``bot`` (aiogram Bot) is only
passed into the two Telegram-aware lookups (tguser/tgid).
"""

from __future__ import annotations


class ToolError(Exception):
    """Bad user input or an unavailable data source.

    The generic tool handler catches this and shows the message as a
    danger-styled error card instead of a stack trace.
    """


class ToolTimeout(ToolError):
    """Lookup exceeded its budget (asyncio.TimeoutError translated)."""
