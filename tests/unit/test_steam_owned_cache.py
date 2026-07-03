"""Round-trip tests for the frontend-supplied owned-Steam-titles cache.

The frontend enumerates the full owned Steam library (installed or not)
and pushes it to the backend; the Ubisoft Steam-linked filter unions it
in. These tests lock the persist/read contract and the normalisation.
"""
from __future__ import annotations

from pathlib import Path

from unifideck.steam import owned_games


def test_save_then_load_round_trips_normalised(tmp_path, monkeypatch):
    monkeypatch.setattr(
        owned_games, "_FRONTEND_CACHE_PATH", tmp_path / "owned.json",
    )
    n = owned_games.save_frontend_owned_titles(
        ["Far Cry® Primal", "Assassin's Creed Odyssey", "Far Cry Primal"],
    )
    # ® stripped + dedup collapses the two Far Cry Primal spellings.
    assert n == 2
    loaded = owned_games.load_frontend_owned_titles()
    assert loaded == frozenset(
        {"far cry primal", "assassins creed odyssey"},
    )


def test_load_missing_cache_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        owned_games, "_FRONTEND_CACHE_PATH", tmp_path / "absent.json",
    )
    assert owned_games.load_frontend_owned_titles() == frozenset()


def test_save_ignores_non_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        owned_games, "_FRONTEND_CACHE_PATH", tmp_path / "owned.json",
    )
    n = owned_games.save_frontend_owned_titles(
        ["Watch Dogs", None, 123, ""],  # type: ignore[list-item]
    )
    assert n == 1
    assert owned_games.load_frontend_owned_titles() == frozenset({"watch dogs"})
    assert Path(tmp_path / "owned.json").is_file()
