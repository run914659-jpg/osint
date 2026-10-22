"""Phase 6.1 — the spinning "searching…" loader (PLAN.md §6.1).

Usage (from the generic tool handler)::

    anim = LoadingAnimation(bot, chat.id, msg.message_id,
                            "🔍 <b>Searching</b>...", query=q)
    await anim.start()
    try:
        data = await mod.lookup(q, cfg, bot)
    finally:
        partial = await anim.stop()

Contract / behaviour:

* one braille frame per edit (``⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏``) and a percent that
  goes 0 → 99 linearly over 8 s;
* **minimum 1.0 s between edits** — the frame moves every edit, so the text
  always differs and Telegram never answers "message is not modified";
* 10 s hard cap → ``partial = True`` (the caller flags the ⏱ footer);
* *every* Telegram error is swallowed — a loader must never break the tool
  flow (only "message is not modified" is treated as "keep going");
* ``start()`` is idempotent and ``stop()`` is safe to call twice.
"""

from __future__ import annotations

import asyncio
import html
import time

from aiogram.exceptions import TelegramBadRequest

__all__ = ["LoadingAnimation"]

FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TICK = 1.0   # minimum seconds between message edits
RAMP = 8.0   # 0 → 99 % is spread linearly over this many seconds
CAP = 10.0   # hard cap; stop() may of course come much earlier


class LoadingAnimation:
    """Keep editing one message in a loop while an OSINT lookup runs."""

    def __init__(
        self,
        bot,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        query: str = "",
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.caption = caption
        self.query = query
        self.partial = False
        self._task: asyncio.Task | None = None

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Spawn the edit loop. Idempotent: a second call changes nothing."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> bool:
        """Cancel the loop and return whether the 10 s cap was hit."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # never propagate loader bugs into tool flow
                pass
        return self.partial

    # -- internals -------------------------------------------------------

    async def _run(self) -> None:
        started = time.monotonic()
        step = 0
        while True:
            if time.monotonic() - started >= CAP:
                self.partial = True
                return
            if not await self._edit(self._frame(time.monotonic() - started, step)):
                return  # real Telegram error → stop silently
            step += 1
            await asyncio.sleep(TICK)

    def _frame(self, elapsed: float, step: int) -> str:
        """One stable, always-different HTML frame."""
        pct = min(99, int(round(elapsed * 99 / RAMP)))
        line = f"{FRAMES[step % len(FRAMES)]} <b>{pct}%</b> — {self.caption}"
        if self.query and "<blockquote" not in line:
            line += f"\n<blockquote>{html.escape(self.query)}</blockquote>"
        return line

    async def _edit(self, text: str) -> bool:
        """Edit the message; ``False`` means "stop looping"."""
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode="HTML",
            )
            return True
        except TelegramBadRequest as exc:
            # Our text did not change (raced the handler's own edit) — keep
            # going; anything else from Telegram → stop, but never raise.
            return "message is not modified" in str(exc).lower()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
