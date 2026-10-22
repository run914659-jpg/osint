"""Tools router + the code → module registry (WAVE3 / PLAN Phase 6).

Importing this package eagerly imports every ``osint`` module named in
:mod:`osintbot.toolspec` and **fails fast** if any of them breaks the
module contract (a callable ``lookup()`` + ``render()``) — a broken tool is
caught at startup (and by tests), never on the first user tap.

Import side effects are otherwise limited to wiring: the osint modules
themselves only define functions, and :func:`register` attaches handlers to
:data:`tools_router` without any I/O or Telegram call.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType

from aiogram import Router

from osintbot import toolspec

__all__ = ["ToolRuntime", "TOOLS_REGISTRY", "tools_router"]


@dataclass(frozen=True)
class ToolRuntime:
    """A :class:`osintbot.toolspec.Tool` + its imported ``osint.<module>``."""

    tool: toolspec.Tool
    module: ModuleType

    @property
    def code(self) -> str:
        return self.tool.code


def _load(tool: toolspec.Tool) -> ToolRuntime:
    """Import ``osint.<tool.module>`` and validate the lookup/render contract."""
    module = importlib.import_module(f"osint.{tool.module}")
    for name in ("lookup", "render"):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(
                f"osint.{tool.module} (tool {tool.code!r}) must expose a "
                f"callable {name}() — see osintbot/toolspec.py module contract"
            )
    return ToolRuntime(tool=tool, module=module)


# Fail fast: exactly one entry per toolspec tool (16), built at import time.
TOOLS_REGISTRY: dict[str, ToolRuntime] = {t.code: _load(t) for t in toolspec.TOOLS}

tools_router = Router(name="tools")

# Imported last: ``generic`` needs TOOLS_REGISTRY (defined above) and
# registers its handlers onto tools_router when the package finishes loading.
from osintbot.handlers.tools.generic import register as _register_handlers  # noqa: E402

_register_handlers(tools_router)
