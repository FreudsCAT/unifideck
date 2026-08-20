"""product.db parsing, pinned against a real on-device capture.

The fixture ``product_db_installed.bin`` was taken from a Steam Deck prefix
on 2026-08-09 immediately after a real 12.43 GB Hearthstone install
completed. Several of these assertions encode traps that cost real
debugging time during the Phase 0 spike, so they are worth their weight:

  * field 1 is a *variant* uid (``hs_beta``) and field 2 is the stable
    product code (``hsb``) — joining on field 1 makes Hearthstone
    unmatchable,
  * the three completion flags flip together in a single write,
  * field 4.4.4 is the total install size in bytes and matched the
    client's own "12.43 GB" readout exactly.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from unifideck.stores.battlenet.product_db import (
    NON_GAME_CODES,
    parse_product_db,
    read_product_db,
)
from unifideck.stores.battlenet.product_db.reader import PRODUCT_DB_RELATIVE
from unifideck.stores.battlenet.product_db.wire import (
    WireError,
    iter_fields,
    read_varint,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "battlenet" / "product_db_installed.bin"


@pytest.fixture
def raw_db() -> bytes:
    return FIXTURE.read_bytes()


# --------------------------------------------------------------------------
# wire format
# --------------------------------------------------------------------------


def test_read_varint_multibyte() -> None:
    # 300 == 0xAC 0x02 in base-128 varint encoding.
    assert read_varint(b"\xac\x02", 0) == (300, 2)


def test_read_varint_truncated_raises() -> None:
    with pytest.raises(WireError):
        read_varint(b"\xac", 0)


def test_read_varint_overlong_raises() -> None:
    with pytest.raises(WireError):
        read_varint(b"\xff" * 12, 0)


def test_iter_fields_rejects_unsupported_wire_type() -> None:
    # Wire type 7 does not exist; desynchronising silently would be worse.
    with pytest.raises(WireError):
        list(iter_fields(b"\x07\x00"))


def test_iter_fields_rejects_overrunning_length() -> None:
    # field 1, wire 2, length 99, but no payload follows.
    with pytest.raises(WireError):
        list(iter_fields(b"\x0a\x63"))


# --------------------------------------------------------------------------
# real capture
# --------------------------------------------------------------------------


def test_parses_the_installed_game(raw_db: bytes) -> None:
    games = parse_product_db(raw_db)
    assert set(games) == {"hsb"}


def test_uid_is_a_variant_string_and_code_is_the_stable_key(raw_db: bytes) -> None:
    """The trap: uid != code. Keying on uid loses the game entirely."""
    game = parse_product_db(raw_db)["hsb"]
    assert game.code == "hsb"
    assert game.uid == "hs_beta"
    assert game.uid != game.code


def test_completion_flags_are_all_set_together(raw_db: bytes) -> None:
    game = parse_product_db(raw_db)["hsb"]
    assert (game.installed, game.playable, game.update_complete) == (True, True, True)
    assert game.is_ready is True


def test_total_bytes_matches_the_clients_own_readout(raw_db: bytes) -> None:
    """12,428,894,444 B == 12.43 GB, exactly what the Download Manager showed."""
    game = parse_product_db(raw_db)["hsb"]
    assert game.total_bytes == 12_428_894_444
    assert round(game.total_bytes / 1000**3, 2) == 12.43


def test_settings_are_decoded(raw_db: bytes) -> None:
    game = parse_product_db(raw_db)["hsb"]
    assert game.install_path == "C:/Program Files (x86)/Hearthstone"
    assert game.play_region == "us"
    assert game.language == "enUS"
    assert game.version == "36.2.0.248348"


def test_agent_and_client_records_are_filtered_out(raw_db: bytes) -> None:
    """The capture contains agent/bna rows; neither is a game."""
    assert set(NON_GAME_CODES) == {"agent", "bna"}
    assert not NON_GAME_CODES & set(parse_product_db(raw_db))


# --------------------------------------------------------------------------
# tolerance — every one of these happens in the field
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"", id="empty-file"),
        pytest.param(b"\xff\xff\xff\xff", id="garbage"),
        pytest.param(b"\x0a\x63", id="torn-write-truncated-length"),
        pytest.param(b"\x07\x00", id="unsupported-wire-type"),
    ],
)
def test_undecodable_input_degrades_to_empty(payload: bytes) -> None:
    assert parse_product_db(payload) == {}


def test_unknown_top_level_fields_are_ignored(raw_db: bytes) -> None:
    """Agent updates add fields (5 and 6 already exist). Must not break."""
    # field 9, varint, value 1 — a field number we have never seen.
    assert parse_product_db(raw_db + b"\x48\x01") == parse_product_db(raw_db)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_product_db(tmp_path) == {}


def test_reads_through_a_drive_c_layout(tmp_path: Path, raw_db: bytes) -> None:
    target = tmp_path / PRODUCT_DB_RELATIVE
    target.parent.mkdir(parents=True)
    target.write_bytes(raw_db)
    assert set(read_product_db(tmp_path)) == {"hsb"}


def test_zero_total_bytes_reads_as_unknown_not_zero() -> None:
    """During a download the field is 0; that means 'not yet known'."""
    # cached_state{ size_state{ total_bytes: 0 } } under product 1, code 'x'.
    size_state = b"\x20\x00"
    cached = b"\x22" + bytes([len(size_state)]) + size_state
    record = b"\x12\x01x" + b"\x22" + bytes([len(cached)]) + cached
    blob = b"\x0a" + bytes([len(record)]) + record
    game = parse_product_db(blob)["x"]
    assert game.total_bytes is None
    assert game.is_ready is False


def test_completion_double_bit_pattern_is_one_when_complete() -> None:
    """Sanity-check the reinterpretation the schema notes describe."""
    assert struct.unpack("<d", struct.pack("<Q", 4607182418800017408))[0] == 1.0
