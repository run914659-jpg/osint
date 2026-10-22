"""Environment configuration for the OSINT bot (Phase 1).

Loads variables from a ``.env`` file (if present) and exposes them as an
immutable :class:`Config`. Fails fast with :class:`SystemExit` when the
mandatory ``BOT_TOKEN`` is missing (PLAN.md R9 / Phase 1.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

__all__ = ["Config"]

_TRUTHY = {"1", "true", "yes", "on"}


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration (one instance per process)."""

    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    run_mode: str = "polling"
    webhook_path: str = "/webhook"
    webhook_secret: str = "change-me"
    public_base_url: str = ""
    db_path: str = "./bot.db"
    port: int = 8000
    numverify_api_key: str | None = None
    use_maigret: bool = False
    binlist_api_key: str | None = None  # optional: lookup.binlist.net higher limits

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Config":
        """Build a :class:`Config` from the process environment.

        ``.env`` is loaded only if it exists; real environment variables
        always win (python-dotenv does not override existing vars).
        Raises ``SystemExit`` with a clear message when ``BOT_TOKEN``
        is absent or empty.
        """
        if env_file:
            load_dotenv(env_file)  # no-op if the file does not exist

        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise SystemExit(
                "BOT_TOKEN is missing or empty. Copy .env.example to .env and set "
                "BOT_TOKEN=<token from @BotFather> (or export it in the environment)."
            )

        raw_admins = os.getenv("ADMIN_IDS") or ""
        admin_ids: set[int] = set()
        for part in raw_admins.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                admin_ids.add(int(part))
            except ValueError:
                raise SystemExit(
                    f"ADMIN_IDS must be a comma-separated list of integer user IDs, "
                    f"got invalid entry: {part!r}"
                ) from None

        run_mode = (os.getenv("RUN_MODE") or "polling").strip().lower() or "polling"
        webhook_path = (os.getenv("WEBHOOK_PATH") or "/webhook").strip() or "/webhook"
        webhook_secret = (os.getenv("WEBHOOK_SECRET") or "").strip() or "change-me"
        public_base_url = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
        db_path = (os.getenv("DB_PATH") or "").strip() or "./bot.db"

        raw_port = (os.getenv("PORT") or "").strip()
        try:
            port = int(raw_port) if raw_port else 8000
        except ValueError:
            raise SystemExit(f"PORT must be an integer, got {raw_port!r}") from None

        numverify = (os.getenv("NUMVERIFY_API_KEY") or "").strip() or None
        use_maigret = _parse_bool(os.getenv("USE_MAIGRET"), default=False)
        binlist = (os.getenv("BINLIST_API_KEY") or "").strip() or None

        return cls(
            bot_token=token,
            admin_ids=admin_ids,
            run_mode=run_mode,
            webhook_path=webhook_path,
            webhook_secret=webhook_secret,
            public_base_url=public_base_url,
            db_path=db_path,
            port=port,
            numverify_api_key=numverify,
            use_maigret=use_maigret,
            binlist_api_key=binlist,
        )
