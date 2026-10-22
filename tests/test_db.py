"""CRUD round-trip tests on a tmp-path SQLite db (PLAN.md Phase 1.4)."""

import sqlite3

import pytest

from osintbot import db


@pytest.fixture
async def conn(tmp_path):
    path = str(tmp_path / "test.db")
    await db.init_db(path)
    connection = await db.get_conn(path)
    yield connection
    await connection.close()


async def test_init_creates_all_tables(tmp_path):
    path = str(tmp_path / "fresh.db")
    await db.init_db(path)
    connection = await db.get_conn(path)
    try:
        cur = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {row[0] for row in await cur.fetchall()}
        assert {"users", "channels", "user_channels", "settings"} <= names
    finally:
        await connection.close()


async def test_upsert_and_get_user(conn):
    await db.upsert_user(conn, 111, "alice", "Alice A")
    user = await db.get_user(conn, 111)
    assert user is not None
    assert user["user_id"] == 111
    assert user["username"] == "alice"
    assert user["full_name"] == "Alice A"
    assert user["joined_at"] and user["last_seen"]

    # upsert updates username/full_name and last_seen, keeps the row single
    await db.upsert_user(conn, 111, "alice2", "Alice B")
    user = await db.get_user(conn, 111)
    assert user["username"] == "alice2"
    assert user["full_name"] == "Alice B"

    assert await db.get_user(conn, 999) is None


async def test_channel_round_trip(conn):
    # add
    added = await db.add_channel(
        conn,
        chat_id=-1001,
        username="osint_news",
        title="OSINT News",
        type="channel",
        join_mode="direct",
        created_by=42,
    )
    assert added["chat_id"] == -1001
    assert added["join_mode"] == "direct"
    assert added["created_at"]

    # get
    got = await db.get_channel(conn, -1001)
    assert got is not None
    assert got["title"] == "OSINT News"
    assert await db.get_channel(conn, -404) is None

    # add a second (explicit created_at → deterministic ordering test)
    await db.add_channel(
        conn,
        chat_id=-1002,
        title="Second",
        type="group",
        join_mode="request",
        created_at="2026-01-01T00:00:02+00:00",
    )
    # give the first channel an explicitly older created_at so ordering by
    # created_at is unambiguous regardless of clock resolution
    await conn.execute(
        "UPDATE channels SET created_at = ? WHERE chat_id = -1001",
        ("2026-01-01T00:00:01+00:00",),
    )
    await conn.commit()
    listing = await db.list_channels(conn)
    assert [c["chat_id"] for c in listing] == [-1001, -1002]

    # set join mode
    await db.set_join_mode(conn, -1001, "direct_private")
    assert (await db.get_channel(conn, -1001))["join_mode"] == "direct_private"

    # set invite url
    await db.set_invite_url(conn, -1001, "https://t.me/+AAAA")
    assert (await db.get_channel(conn, -1001))["invite_url"] == "https://t.me/+AAAA"

    # delete also clears user_channels rows for that chat
    await db.set_verified(conn, 7, -1001)
    assert await db.is_verified(conn, 7, -1001)
    await db.delete_channel(conn, -1001)
    assert await db.get_channel(conn, -1001) is None
    assert not await db.is_verified(conn, 7, -1001)
    assert [c["chat_id"] for c in await db.list_channels(conn)] == [-1002]


async def test_invalid_join_mode_raises_integrity_error(conn):
    with pytest.raises(sqlite3.IntegrityError):
        await db.add_channel(conn, chat_id=-2001, join_mode="bogus")
    # CHECK constraint also guards the UPDATE path
    await db.add_channel(conn, chat_id=-2002, join_mode="direct")
    with pytest.raises(sqlite3.IntegrityError):
        await db.set_join_mode(conn, -2002, "also_bogus")


async def test_verified_flow(conn):
    await db.add_channel(
        conn, chat_id=-3001, title="One", join_mode="direct",
        created_at="2026-01-01T00:00:01+00:00",
    )
    await db.add_channel(
        conn, chat_id=-3002, title="Two", join_mode="request",
        created_at="2026-01-01T00:00:02+00:00",
    )

    # nothing verified yet → both pending
    assert not await db.is_verified(conn, 5, -3001)
    pending = await db.get_pending_channels(conn, 5)
    assert [c["chat_id"] for c in pending] == [-3001, -3002]

    # verify one → only the other remains pending
    await db.set_verified(conn, 5, -3001)
    assert await db.is_verified(conn, 5, -3001)
    pending = await db.get_pending_channels(conn, 5)
    assert [c["chat_id"] for c in pending] == [-3002]

    # verify the rest → no pending channels
    await db.set_verified(conn, 5, -3002)
    assert await db.get_pending_channels(conn, 5) == []

    # another user still sees both
    assert len(await db.get_pending_channels(conn, 6)) == 2

    # clearing wipes the cache
    await db.clear_verifications(conn, 5)
    assert not await db.is_verified(conn, 5, -3001)
    assert len(await db.get_pending_channels(conn, 5)) == 2


async def test_clear_verification_revokes_single_row(conn):
    """REVIEW H2: verify → leave → verify must not keep a stale bypass."""
    await db.add_channel(conn, chat_id=-4001, title="One", join_mode="direct")
    await db.add_channel(conn, chat_id=-4002, title="Two", join_mode="direct")
    await db.set_verified(conn, 5, -4001)
    await db.set_verified(conn, 5, -4002)
    assert await db.get_pending_channels(conn, 5) == []

    # the user left channel One → revoke only that row
    await db.clear_verification(conn, 5, -4001)
    assert not await db.is_verified(conn, 5, -4001)
    assert await db.is_verified(conn, 5, -4002)  # other rows untouched
    pending = await db.get_pending_channels(conn, 5)
    assert [c["chat_id"] for c in pending] == [-4001]

    # revoking for a user who has no rows is a harmless no-op
    await db.clear_verification(conn, 6, -4001)
    assert len(await db.get_pending_channels(conn, 6)) == 2


async def test_settings_round_trip(conn):
    assert await db.get_setting(conn, "missing") is None
    assert await db.get_setting(conn, "missing", "fallback") == "fallback"

    await db.set_setting(conn, "loader_enabled", "1")
    assert await db.get_setting(conn, "loader_enabled") == "1"

    await db.set_setting(conn, "loader_enabled", "0")
    assert await db.get_setting(conn, "loader_enabled") == "0"


# --------------------------------------------- verification TTL (H2 fix) ---


async def test_stale_verification_expires_back_into_gate(conn):
    await db.add_channel(
        conn, chat_id=-1001, username="chan", title="Chan",
        type="channel", join_mode="direct", created_by=1,
    )
    await db.set_verified(conn, 42, -1001)
    assert await db.get_pending_channels(conn, 42) == []  # fresh cache → gate passes

    # Backdate the cache beyond VERIFICATION_TTL_HOURS → gate must re-check.
    await conn.execute(
        "UPDATE user_channels SET verified_at='2000-01-01T00:00:00.000000+00:00' "
        "WHERE user_id=42 AND chat_id=-1001"
    )
    await conn.commit()
    pending = await db.get_pending_channels(conn, 42)
    assert [c["chat_id"] for c in pending] == [-1001]

    # Re-verifying refreshes the cache → gate passes again.
    await db.set_verified(conn, 42, -1001)
    assert await db.get_pending_channels(conn, 42) == []
