"""CacheManager deferred-write mode (``set(..., flush=False)``).

The per-game sync loops used to rewrite a growing namespace file once
per key under eager persistence — O(n²) disk I/O across a 1000-game
library. Deferred mode keeps writes in memory and persists at the
phase boundary via ``flush`` (or the ``AUTO_FLUSH_EVERY`` valve, which
bounds data loss on crash).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from unifideck.core.cache_manager import CacheManager, CacheStore


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_eager_set_persists_immediately(tmp_path: Path) -> None:
    mgr = CacheManager(str(tmp_path))
    mgr.register("ns")
    mgr.set("ns", "k", 1)
    assert _read(tmp_path / "ns_cache.json")["data"] == {"k": 1}


def test_deferred_set_stays_in_memory_until_flush(tmp_path: Path) -> None:
    mgr = CacheManager(str(tmp_path))
    mgr.register("ns")
    mgr.set("ns", "k0", 0)  # eager write creates the file
    mgr.set("ns", "k1", 1, flush=False)
    path = tmp_path / "ns_cache.json"
    assert "k1" not in _read(path)["data"]  # not on disk yet
    assert mgr.get("ns", "k1") == 1  # but readable in memory
    mgr.flush("ns")
    assert _read(path)["data"]["k1"] == 1


def test_flush_is_noop_when_clean(tmp_path: Path) -> None:
    mgr = CacheManager(str(tmp_path))
    mgr.register("ns")
    mgr.set("ns", "k", 1)
    path = tmp_path / "ns_cache.json"
    before = path.stat().st_mtime_ns
    mgr.flush("ns")
    assert path.stat().st_mtime_ns == before  # no rewrite happened


def test_auto_flush_valve_bounds_unflushed_writes(tmp_path: Path) -> None:
    store = CacheStore("ns", tmp_path / "ns_cache.json")
    for i in range(CacheStore.AUTO_FLUSH_EVERY - 1):
        store.set(f"k{i}", i, flush=False)
    assert not store.path.exists()  # still below the valve
    store.set("last", 1, flush=False)  # valve fires
    assert len(_read(store.path)["data"]) == CacheStore.AUTO_FLUSH_EVERY


def test_eager_set_clears_dirty_state(tmp_path: Path) -> None:
    store = CacheStore("ns", tmp_path / "ns_cache.json")
    store.set("a", 1, flush=False)
    store.set("b", 2)  # eager — persists everything, resets dirty
    assert _read(store.path)["data"] == {"a": 1, "b": 2}
    before = store.path.stat().st_mtime_ns
    store.flush()  # nothing dirty → no rewrite
    assert store.path.stat().st_mtime_ns == before


def test_flush_still_writes_bak_snapshot(tmp_path: Path) -> None:
    store = CacheStore("ns", tmp_path / "ns_cache.json")
    store.set("a", 1)  # creates the main file
    store.set("b", 2, flush=False)
    store.flush()
    bak = store.path.with_suffix(store.path.suffix + ".bak")
    assert bak.exists()
    assert _read(bak)["data"] == {"a": 1}  # prior generation preserved


def test_flush_all_covers_every_registered_store(tmp_path: Path) -> None:
    mgr = CacheManager(str(tmp_path))
    mgr.register("a")
    mgr.register("b")
    mgr.set("a", "k", 1, flush=False)
    mgr.set("b", "k", 2, flush=False)
    mgr.flush_all()
    assert _read(tmp_path / "a_cache.json")["data"] == {"k": 1}
    assert _read(tmp_path / "b_cache.json")["data"] == {"k": 2}
