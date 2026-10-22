"""aiosqlite wrapper: schema init + CRUD (PLAN.md Phase 1 / section 3).

Every data function takes the connection explicitly as its first argument
(``conn`` from :func:`get_conn` / :func:`aiosqlite.connect`) and commits
after writes. The DDL matches PLAN.md section 3 exactly, including the
``join_mode`` CHECK constraint.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import aiosqlite

__all__ = [
    "init_db",
    "get_conn",
    "upsert_user",
    "get_user",
    "add_channel",
    "get_channel",
    "list_channels",
    "delete_channel",
    "set_join_mode",
    "set_invite_url",
    "is_verified",
    "set_verified",
    "clear_verification",
    "clear_verifications",
    "get_pending_channels",
    "get_setting",
    "set_setting",
]

DDL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
  is_blocked INTEGER DEFAULT 0, joined_at TEXT, last_seen TEXT);

CREATE TABLE IF NOT EXISTS channels (
  chat_id INTEGER PRIMARY KEY, username TEXT, title TEXT, type TEXT,
  join_mode TEXT NOT NULL CHECK(join_mode IN ('direct','request','direct_private')),
  invite_url TEXT, photo_url TEXT, created_by INTEGER, created_at TEXT);

CREATE TABLE IF NOT EXISTS user_channels (          -- verified membership cache
  user_id INTEGER, chat_id INTEGER, verified_at TEXT,
  PRIMARY KEY (user_id, chat_id));

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

_CHANNEL_COLS = (
    "chat_id",
    "username",
    "title",
    "type",
    "join_mode",
    "invite_url",
    "photo_url",
    "created_by",
    "created_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# Cached verifications older than this re-enter the force-join gate, so a
# user who joined → verified → left cannot bypass forever without ever
# tapping Verify again (REVIEW H2 residual). Lexicographic comparison is
# safe: verified_at and the cutoff share _now()'s ISO-8601 UTC format.
VERIFICATION_TTL_HOURS = 6


async def get_conn(path: str) -> aiosqlite.Connection:
    """Open a connection (callers own closing it)."""
    conn = await aiosqlite.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


async def init_db(path: str) -> None:
    """Create tables if missing. Opens and closes its own connection."""
    conn = await get_conn(path)
    try:
        await conn.executescript(DDL)
        await conn.commit()
    finally:
        await conn.close()


# ---------------------------------------------------------------- users ----

async def upsert_user(
    conn: aiosqlite.Connection,
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> None:
    now = _now()
    await conn.execute(
        """
        INSERT INTO users (user_id, username, full_name, joined_at, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            last_seen = excluded.last_seen
        """,
        (user_id, username, full_name, now, now),
    )
    await conn.commit()


async def get_user(conn: aiosqlite.Connection, user_id: int) -> dict | None:
    cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return dict(row) if row is not None else None


# ------------------------------------------------------------- channels ----

async def add_channel(conn: aiosqlite.Connection, **fields) -> dict:
    """Insert or replace a channel row. Requires ``chat_id`` and ``join_mode``.

    Invalid ``join_mode`` values raise ``sqlite3.IntegrityError`` from the
    CHECK constraint.
    """
    unknown = set(fields) - set(_CHANNEL_COLS)
    assert not unknown, f"unknown channel fields: {sorted(unknown)}"
    assert "chat_id" in fields, "add_channel requires chat_id"
    assert "join_mode" in fields, "add_channel requires join_mode"

    values = {col: fields.get(col) for col in _CHANNEL_COLS}
    if values["created_at"] is None:
        values["created_at"] = _now()

    cols = ", ".join(_CHANNEL_COLS)
    placeholders = ", ".join("?" for _ in _CHANNEL_COLS)
    await conn.execute(
        f"INSERT OR REPLACE INTO channels ({cols}) VALUES ({placeholders})",
        tuple(values[col] for col in _CHANNEL_COLS),
    )
    await conn.commit()
    return await get_channel(conn, fields["chat_id"])  # type: ignore[return-value]


async def get_channel(conn: aiosqlite.Connection, chat_id: int) -> dict | None:
    cur = await conn.execute("SELECT * FROM channels WHERE chat_id = ?", (chat_id,))
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def list_channels(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute("SELECT * FROM channels ORDER BY created_at ASC, rowid ASC")
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def delete_channel(conn: aiosqlite.Connection, chat_id: int) -> None:
    """Delete the channel and any cached verifications for that chat."""
    await conn.execute("DELETE FROM user_channels WHERE chat_id = ?", (chat_id,))
    await conn.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
    await conn.commit()


async def set_join_mode(conn: aiosqlite.Connection, chat_id: int, mode: str) -> None:
    """Set join_mode; invalid modes raise sqlite3.IntegrityError (CHECK)."""
    await conn.execute(
        "UPDATE channels SET join_mode = ? WHERE chat_id = ?", (mode, chat_id)
    )
    await conn.commit()


async def set_invite_url(
    conn: aiosqlite.Connection, chat_id: int, url: str | None
) -> None:
    await conn.execute(
        "UPDATE channels SET invite_url = ? WHERE chat_id = ?", (url, chat_id)
    )
    await conn.commit()


# --------------------------------------------------- verification cache ----

async def is_verified(conn: aiosqlite.Connection, user_id: int, chat_id: int) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM user_channels WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id),
    )
    return await cur.fetchone() is not None


async def set_verified(conn: aiosqlite.Connection, user_id: int, chat_id: int) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO user_channels (user_id, chat_id, verified_at) "
        "VALUES (?, ?, ?)",
        (user_id, chat_id, _now()),
    )
    await conn.commit()


async def clear_verification(
    conn: aiosqlite.Connection, user_id: int, chat_id: int
) -> None:
    """Revoke ONE cached verification (e.g. the user left that channel).

    Used by the verify flow to invalidate stale rows: a cached "joined"
    must never outlive the actual membership (REVIEW H2).
    """
    await conn.execute(
        "DELETE FROM user_channels WHERE user_id = ? AND chat_id = ?",
        (user_id, chat_id),
    )
    await conn.commit()


async def clear_verifications(conn: aiosqlite.Connection, user_id: int) -> None:
    await conn.execute("DELETE FROM user_channels WHERE user_id = ?", (user_id,))
    await conn.commit()


async def get_pending_channels(
    conn: aiosqlite.Connection, user_id: int
) -> list[dict]:
    """Channels this user has NOT been verified in yet (force-join gate).

    Verifications expire after ``VERIFICATION_TTL_HOURS`` — a stale cache
    re-gates the user, who then runs the full re-check in the verify flow.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=VERIFICATION_TTL_HOURS)
    ).isoformat(timespec="microseconds")
    cur = await conn.execute(
        """
        SELECT c.* FROM channels c
        WHERE NOT EXISTS (
            SELECT 1 FROM user_channels uc
            WHERE uc.chat_id = c.chat_id AND uc.user_id = ?
              AND uc.verified_at >= ?
        )
        ORDER BY c.created_at ASC, c.rowid ASC
        """,
        (user_id, cutoff),
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------- settings ----

async def get_setting(
    conn: aiosqlite.Connection, key: str, default: str | None = None
) -> str | None:
    cur = await conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    if row is None:
        return default
    return row["value"] if isinstance(row, sqlite3.Row) else row[0]


async def set_setting(
    conn: aiosqlite.Connection, key: str, value: str | None
) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    await conn.commit()
