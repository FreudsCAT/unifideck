"""Unit tests for the Metacritic-at-sync pipeline fixes.

Covers the two halves of the "non-Steam shortcuts missing Metacritic"
fix beyond the facet assembler (see ``test_library_facets.py``):

* the backfill now stamps the resolved ``steam_appid`` onto the
  ``metadata[store:game_id]`` entry it writes (defensive — keeps any
  steam_appid-keyed reader correct), and
* the 429 helpers (``Retry-After`` parsing, now shared via
  ``steam/http_retry.py``) + the appdetails response-shape parsing
  that back the rate-limit retry loop.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from unifideck.services.metadata_backfill import _merge_into_metadata_cache
from unifideck.steam.appdetails import _parse_appdetails
from unifideck.steam.http_retry import (
    MAX_RETRY_AFTER_S,
    retry_after_seconds,
)


class _Cache:
    """Minimal CacheManager stand-in: ``(namespace, key)`` dict."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], Any] = {}

    def get(self, ns: str, key: str) -> Any:
        return self._d.get((ns, key))

    def set(self, ns: str, key: str, val: Any, *, flush: bool = True) -> None:
        self._d[(ns, key)] = val

    def flush(self, ns: str) -> None:
        pass


def _game(app_id: int, store: str, store_game_id: str) -> Any:
    return SimpleNamespace(
        app_id=app_id,
        store=store,
        store_game_id=store_game_id,
    )


# ── backfill steam_appid stamping ─────────────────────────────────


def test_backfill_stamps_resolved_steam_appid() -> None:
    cache = _Cache()
    cache.set("steam_real_appid", "-123", 945360)
    _merge_into_metadata_cache(
        cache,
        _game(-123, "epic", "abc"),
        {"metacritic_score": 70},
    )
    entry = cache.get("metadata", "epic:abc")
    assert entry["metacritic_score"] == 70
    assert entry["steam_appid"] == 945360


def test_backfill_no_real_appid_leaves_steam_appid_unset() -> None:
    cache = _Cache()  # no steam_real_appid mapping
    _merge_into_metadata_cache(
        cache,
        _game(-123, "epic", "abc"),
        {"metacritic_score": 70},
    )
    entry = cache.get("metadata", "epic:abc")
    assert entry["metacritic_score"] == 70
    assert "steam_appid" not in entry


def test_backfill_preserves_existing_steam_appid() -> None:
    cache = _Cache()
    cache.set("metadata", "epic:abc", {"steam_appid": 111})
    cache.set("steam_real_appid", "-123", 999)
    _merge_into_metadata_cache(
        cache,
        _game(-123, "epic", "abc"),
        {"metacritic_score": 70},
    )
    entry = cache.get("metadata", "epic:abc")
    assert entry["steam_appid"] == 111  # not overwritten
    assert entry["metacritic_score"] == 70


# ── appdetails 429 helpers ────────────────────────────────────────


def test_retry_after_seconds_parsing() -> None:
    assert retry_after_seconds(SimpleNamespace(headers={"Retry-After": "5"})) == 5.0
    assert retry_after_seconds(SimpleNamespace(headers={})) is None
    assert retry_after_seconds(SimpleNamespace(headers={"Retry-After": "nope"})) is None
    # Clamped to the cap so a hostile header can't park us forever.
    assert (
        retry_after_seconds(SimpleNamespace(headers={"Retry-After": "99999"}))
        == MAX_RETRY_AFTER_S
    )


def test_parse_appdetails_shapes() -> None:
    ok = {"945360": {"success": True, "data": {"name": "X"}}}
    assert _parse_appdetails(ok, 945360) == {"name": "X"}
    assert _parse_appdetails({"945360": {"success": False}}, 945360) is None
    assert _parse_appdetails({}, 945360) is None
    assert _parse_appdetails("not-a-dict", 945360) is None
    # success but non-dict data → None
    assert _parse_appdetails({"945360": {"success": True, "data": 1}}, 945360) is None
