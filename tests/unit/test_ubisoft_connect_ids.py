"""Tests for leveldb ``ubisoftConnectGameId`` extraction and its wiring.

Ported from staging's ``_extract_cache_game_ids`` (staging ``ubisoft.py``
~L1713-1796). Three surfaces are covered:

1. ``extract_cache_game_ids`` / ``_extract_ids_from_binary`` parse both
   key orderings out of a leveldb blob.
2. ``_GameBuilder`` records the connect id on the id_map entry so
   ``resolve_launch_id`` can prefer it.
3. The launcher handler resolves ``UPLAY_ID`` from the persisted id_map
   when the env var is absent (the realistic case — Steam can't pass it).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.id_map_sources import (
    _extract_ids_from_binary,
    extract_cache_game_ids,
)
from unifideck.stores.ubisoft.library.game_builder import _GameBuilder
from unifideck.stores.ubisoft.parser import GameConfig

_SPACE = "abcd1234-5678-90ab-cdef-1234567890ab"


def _norm(name: str) -> str:
    return UbisoftIdMap._normalize_for_matching(name)


class _IdMap:
    def __init__(self) -> None:
        self.bulk: dict[str, dict[str, Any]] = {}

    def normalize_for_matching(self, name: str) -> str:
        return _norm(name)

    def update_bulk(self, mapping: dict[str, dict[str, Any]]) -> None:
        self.bulk.update(mapping)


def _cfg(install_id: int, space_id: str, name: str) -> GameConfig:
    c = GameConfig()
    c.install_id = install_id
    c.launch_id = install_id
    c.space_id = space_id
    c.name = name
    return c


# ── binary parsing ────────────────────────────────────────────────


def test_extract_ids_space_then_connect():
    blob = (
        b'junk{"spaceId":"' + _SPACE.encode() + b'","name":"X",'
        b'"ubisoftConnectGameId":4242}tail'
    )
    out: dict[str, str] = {}
    _extract_ids_from_binary(blob, out)
    assert out == {_SPACE: "4242"}


def test_extract_ids_connect_then_space():
    blob = (
        b'{"ubisoftConnectGameId":777,"spaceId":"' + _SPACE.encode() + b'"}'
    )
    out: dict[str, str] = {}
    _extract_ids_from_binary(blob, out)
    assert out == {_SPACE: "777"}


def test_extract_ids_first_wins():
    blob = (
        b'"spaceId":"' + _SPACE.encode() + b'""ubisoftConnectGameId":111'
        b'"spaceId":"' + _SPACE.encode() + b'""ubisoftConnectGameId":222'
    )
    out: dict[str, str] = {}
    _extract_ids_from_binary(blob, out)
    assert out == {_SPACE: "111"}


def test_extract_cache_game_ids_reads_leveldb(tmp_path: Path):
    leveldb = tmp_path / "ls" / "leveldb"
    leveldb.mkdir(parents=True)
    (leveldb / "000005.ldb").write_bytes(
        b'"spaceId":"' + _SPACE.encode() + b'""ubisoftConnectGameId":9001',
    )
    result = extract_cache_game_ids(str(tmp_path), "ls")
    assert result == {_SPACE: "9001"}


def test_extract_cache_game_ids_missing_dir(tmp_path: Path):
    assert extract_cache_game_ids(str(tmp_path), "nope") == {}


# ── builder records the connect id ────────────────────────────────


def test_builder_records_connect_id():
    builder = _GameBuilder(config=object(), id_map=_IdMap())
    cfg = _cfg(1, _SPACE, "Some Game")
    builder.build_games_from_configs(
        [cfg], installed={}, connect_ids={_SPACE: "5555"},
    )
    assert builder._id_map.bulk[_SPACE]["ubisoftconnect_game_id"] == "5555"


def test_builder_omits_connect_id_when_absent():
    builder = _GameBuilder(config=object(), id_map=_IdMap())
    cfg = _cfg(1, _SPACE, "Some Game")
    builder.build_games_from_configs([cfg], installed={}, connect_ids={})
    assert "ubisoftconnect_game_id" not in builder._id_map.bulk[_SPACE]


# ── launcher handler resolves UPLAY_ID from the id_map ────────────


def test_handler_resolves_uplay_id_from_id_map(tmp_path, monkeypatch):
    from unifideck.launcher.proton.handlers import ubisoft as handler

    id_map_file = tmp_path / "ubisoft_id_map.json"
    id_map_file.write_text(
        json.dumps(
            {_SPACE: {"launch_id": "100", "ubisoftconnect_game_id": "9001"}},
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(handler, "ID_MAP_FILE", id_map_file)
    # prefers connect id over launch_id
    assert handler._uplay_id_from_id_map(_SPACE) == "9001"


def test_handler_falls_back_to_launch_id(tmp_path, monkeypatch):
    from unifideck.launcher.proton.handlers import ubisoft as handler

    id_map_file = tmp_path / "ubisoft_id_map.json"
    id_map_file.write_text(
        json.dumps({_SPACE: {"launch_id": "100", "install_id": "50"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(handler, "ID_MAP_FILE", id_map_file)
    assert handler._uplay_id_from_id_map(_SPACE) == "100"


def test_handler_returns_none_when_unknown(tmp_path, monkeypatch):
    from unifideck.launcher.proton.handlers import ubisoft as handler

    monkeypatch.setattr(handler, "ID_MAP_FILE", tmp_path / "missing.json")
    assert handler._uplay_id_from_id_map(_SPACE) is None
