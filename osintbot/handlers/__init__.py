"""Handler package — aggregates every router (PLAN.md §3).

Import order matters: ``start`` before ``admin`` so ``/start``-related
handlers get first shot (handlers use exact filters, so this is just a
defensive ordering).
"""

from __future__ import annotations

from aiogram import Router

from osintbot.handlers.admin import router as admin_router
from osintbot.handlers.joinrequests import router as joinrequests_router
from osintbot.handlers.start import router as start_router

__all__ = [
    "start_router",
    "admin_router",
    "joinrequests_router",
    "all_routers",
]


def all_routers() -> list[Router]:
    """Routers in registration order for the Dispatcher."""
    return [
        start_router,
        admin_router,
        joinrequests_router,
        # WAVE3: tools_router  <-- t:<code> tool handlers plug in here
    ]
