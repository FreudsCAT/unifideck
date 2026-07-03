"""Unit tests for playtime → store sync (Heroic #1240).

Covers: the schema-v2 migration + query helpers, the GOG/Epic HTTP request
shapes (mocked ``urlopen``), and the ``PlaytimeSyncService`` drain orchestration
(fake store). No real network.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from unifideck.services.playtime.db import ActivityDatabase
from unifideck.services.playtime_sync.service import (
    PlaytimeSyncService,
    _iso_to_unix,
)
from unifideck.stores.epic import playtime_api as epic_api
from unifideck.stores.gog import galaxy_api as gog_api

_ISO = "2026-06-01T10:00:00.000Z"
_ISO_END = "2026-06-01T10:30:00.000Z"


# --- helpers ----------------------------------------------------------------

class _FakeResp:
    """Minimal context-manager stand-in for ``urlopen``'s return value."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


def _capturing_urlopen(status: int, body: bytes, sink: dict[str, Any]):
    """An ``urlopen`` replacement that records the Request and returns a resp."""
    def _fake(req: Any, timeout: Any = None, context: Any = None) -> _FakeResp:
        sink["req"] = req
        return _FakeResp(status, body)
    return _fake


def _seed_session(
    db: ActivityDatabase, store: str, store_game_id: str,
    started: str = _ISO, ended: str | None = _ISO_END, dur: int | None = 1800,
) -> int:
    """Insert a finalized session; return its games.id."""
    gid = db.get_or_create_game(store, store_game_id, f"Game {store_game_id}", 0)
    db.execute(
        "INSERT INTO play_sessions "
        "(game_id, started_at, ended_at, duration_secs, end_reason) "
        "VALUES (?, ?, ?, ?, 'normal')",
        (gid, started, ended, dur),
    )
    db._require_conn().commit()
    return gid


class _FakeStore:
    """Stand-in store exposing the sync API the service duck-types."""

    def __init__(self, available: bool = True, total: int | None = 3600) -> None:
        self.available = available
        self.total = total
        self.pushed: list[tuple[str, int, int]] = []

    async def is_available(self) -> bool:
        return self.available

    async def report_play_session(
        self, game_id: str, started_at_unix: int, duration_secs: int,
    ) -> bool:
        self.pushed.append((game_id, started_at_unix, duration_secs))
        return True

    async def get_play_total_secs(self, game_id: str) -> int | None:
        return self.total


class _FakeBus:
    def __init__(self) -> None:
        self.subscriptions: list[Any] = []

    def on(self, event: Any, handler: Any) -> None:
        self.subscriptions.append((event, handler))

    async def emit(self, *_a: Any, **_k: Any) -> None:
        return None

    def unsubscribe_all(self, _inst: Any) -> None:
        return None


class _FakeRegistry:
    def __init__(self, stores: dict[str, Any]) -> None:
        self._stores = stores

    def get_store(self, store_id: str) -> Any:
        return self._stores.get(store_id)


def _service(db_path: str, stores: dict[str, Any]) -> PlaytimeSyncService:
    svc = PlaytimeSyncService(_FakeBus(), _FakeRegistry(stores), None, db_path)
    svc._db = ActivityDatabase(db_path)
    svc._db.open()
    return svc


# --- migration + query helpers ----------------------------------------------

def test_migration_v2_schema(tmp_path):
    db = ActivityDatabase(str(tmp_path / "p.db"))
    assert db.open() == 2
    cols = [r[1] for r in db.query("PRAGMA table_info(play_sessions)")]
    tbls = [r[0] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "reported_at" in cols
    assert "store_playtime" in tbls
    db.close()


def test_get_unreported_and_mark(tmp_path):
    db = ActivityDatabase(str(tmp_path / "p.db"))
    db.open()
    gid = _seed_session(db, "gog", "123")
    rows = db.get_unreported_sessions()
    assert len(rows) == 1
    row = rows[0]
    assert row["store"] == "gog"
    assert row["store_game_id"] == "123"
    assert row["game_db_id"] == gid
    db.mark_session_reported(row["id"])
    assert db.get_unreported_sessions() == []
    db.close()


def test_get_unreported_filters(tmp_path):
    db = ActivityDatabase(str(tmp_path / "p.db"))
    db.open()
    _seed_session(db, "gog", "ok")               # qualifies
    _seed_session(db, "gog", "short", dur=3)     # below min_secs
    _seed_session(db, "gog", "active", ended=None, dur=None)  # not finalized
    _seed_session(db, "amazon", "other")         # non-sync store
    rows = db.get_unreported_sessions()
    assert {r["store_game_id"] for r in rows} == {"ok"}
    db.close()


def test_upsert_store_playtime(tmp_path):
    db = ActivityDatabase(str(tmp_path / "p.db"))
    db.open()
    gid = _seed_session(db, "gog", "123")
    db.upsert_store_playtime(gid, 5400)
    db.upsert_store_playtime(gid, 7200)  # overwrite, not accumulate
    row = db.query_one(
        "SELECT store_total_secs FROM store_playtime WHERE game_id = ?", (gid,))
    assert row["store_total_secs"] == 7200
    db.close()


# --- GOG HTTP request shapes ------------------------------------------------

def test_post_gog_session_shape():
    sink: dict[str, Any] = {}
    with patch("urllib.request.urlopen", _capturing_urlopen(200, b"", sink)):
        ok = gog_api.post_gog_session("U", "G", "TOKEN", 1700000000, 30)
    assert ok is True
    req = sink["req"]
    assert req.get_method() == "POST"
    assert req.full_url == "https://gameplay.gog.com/games/G/users/U/sessions"
    assert req.get_header("Authorization") == "Bearer TOKEN"
    assert json.loads(req.data) == {"session_date": 1700000000, "time": 30}


def test_fetch_gog_playtime_minutes():
    sink: dict[str, Any] = {}
    body = json.dumps({"time_sum": 42}).encode()
    with patch("urllib.request.urlopen", _capturing_urlopen(200, body, sink)):
        mins = gog_api.fetch_gog_playtime_minutes("U", "G", "TOKEN")
    assert mins == 42
    assert sink["req"].get_method() == "GET"


# --- Epic HTTP request shapes (force the urllib fallback) -------------------

def test_put_epic_session_shape():
    sink: dict[str, Any] = {}
    with patch.object(epic_api.shutil, "which", return_value=None), \
            patch("urllib.request.urlopen", _capturing_urlopen(204, b"", sink)):
        code = epic_api.put_epic_session(
            "ACC", "ART", "bearer", "TOK",
            "2026-06-01T10:00:00.000Z", "2026-06-01T10:30:00.000Z", "deck",
        )
    assert code == 204
    req = sink["req"]
    assert req.get_method() == "PUT"
    assert req.full_url.endswith("/playtime/account/ACC")
    assert req.get_header("Authorization") == "bearer TOK"
    assert json.loads(req.data) == {
        "machineId": "deck", "artifactId": "ART",
        "startTime": "2026-06-01T10:00:00.000Z",
        "endTime": "2026-06-01T10:30:00.000Z",
    }


def test_fetch_epic_playtime_all():
    sink: dict[str, Any] = {}
    body = json.dumps([
        {"artifactId": "A", "totalTime": 120},
        {"artifactId": "B", "totalTime": 0},
    ]).encode()
    with patch.object(epic_api.shutil, "which", return_value=None), \
            patch("urllib.request.urlopen", _capturing_urlopen(200, body, sink)):
        code, mapping = epic_api.fetch_epic_playtime_all("ACC", "bearer", "TOK")
    assert code == 200
    assert mapping == {"A": 120, "B": 0}


# --- service drain orchestration --------------------------------------------

async def test_drain_pushes_marks_and_reconciles(tmp_path):
    store = _FakeStore(total=3600)
    svc = _service(str(tmp_path / "p.db"), {"gog": store})
    gid = _seed_session(svc._db, "gog", "123")

    pushed = await svc._drain()

    assert pushed == {"gog": 1}
    assert store.pushed == [("123", _iso_to_unix(_ISO), 1800)]
    assert svc._db.get_unreported_sessions() == []
    row = svc._db.query_one(
        "SELECT store_total_secs FROM store_playtime WHERE game_id = ?", (gid,))
    assert row["store_total_secs"] == 3600


async def test_drain_skips_unavailable_store(tmp_path):
    store = _FakeStore(available=False)
    svc = _service(str(tmp_path / "p.db"), {"gog": store})
    _seed_session(svc._db, "gog", "123")

    pushed = await svc._drain()

    assert pushed == {"gog": 0}
    assert store.pushed == []
    # Left unreported so it retries once the store is back.
    assert len(svc._db.get_unreported_sessions()) == 1


async def test_drain_stamps_unparseable_timestamp(tmp_path):
    store = _FakeStore()
    svc = _service(str(tmp_path / "p.db"), {"gog": store})
    _seed_session(svc._db, "gog", "123", started="not-a-date")

    pushed = await svc._drain()

    assert pushed == {"gog": 0}
    assert store.pushed == []
    # Stamped so it doesn't wedge the queue forever.
    assert svc._db.get_unreported_sessions() == []


async def test_drain_missing_store_is_noop(tmp_path):
    svc = _service(str(tmp_path / "p.db"), {})  # no gog store registered
    _seed_session(svc._db, "gog", "123")

    pushed = await svc._drain()

    assert pushed == {"gog": 0}
    assert len(svc._db.get_unreported_sessions()) == 1


def test_iso_to_unix():
    assert _iso_to_unix(_ISO) == 1780308000  # 2026-06-01T10:00:00Z
    assert _iso_to_unix("garbage") is None
