"""Pinning test — ``services/shortcut/games_map.generate_app_id``.

These hashes are **load-bearing for backwards compatibility**.
``generate_app_id`` is the algorithm every released-version
(Release-0.6.1) user's Steam shortcut library is keyed on. Any
change to the byte sequence we feed CRC32 silently re-keys every
shortcut on upgrade, which loses Steam playtime, categories,
hidden flags, and on-disk grid artwork bound to the old appid.

See ``/home/deck/.claude/plans/can-you-find-out-wiggly-mccarthy.md``
for the discovery of the regression that motivated this test.

The test does two things:

1. **Cross-checks against an inline v0.6.1 oracle** — catches the
   common failure mode where someone "refactors" ``generate_app_id``
   and the hash output drifts. The oracle is copied verbatim from
   ``Release-0.6.1:py_modules/unifideck/shortcuts/shortcuts_manager.py:1211-1226``.
2. **Pins pre-computed reference hashes** — catches the secondary
   failure mode where both the oracle and the implementation get
   "refactored" the same wrong way. The numeric values here were
   computed by running the v0.6.1 algorithm on the inputs below;
   if either changes, this test fails immediately.
"""
from __future__ import annotations

import binascii
import struct

import pytest

from unifideck.services.shortcut.games_map import generate_app_id

# Realistic launcher path — matches the production install
# location (`<plugin_dir>/bin/unifideck-launcher`) used by
# `_AUTH_SHORTCUT_META` callers, sync_service, etc.
_LAUNCHER = "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"


def _v061_reference(game_title: str, exe_path: str, full_id: str | None) -> int:
    """Verbatim reproduction of v0.6.1's generate_app_id.

    Source: ``git show Release-0.6.1:py_modules/unifideck/shortcuts/shortcuts_manager.py``
    lines 1211–1226. Kept here as a self-contained oracle so this
    test never depends on the implementation it is verifying.
    """
    # v0.6.1 also had an `is_protected_shortcut_id(full_id)` check
    # that forced the fallback branch. Every v0.6.1 game caller
    # passed a non-protected full_id, so we exercise that branch
    # explicitly here.
    if full_id:
        key = f"{exe_path}|{full_id}"
    else:
        key = f"{exe_path}|{game_title}"
    crc = binascii.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    signed: int = struct.unpack("i", struct.pack("I", crc | 0x80000000))[0]
    return signed


# Pre-computed expected hashes. DO NOT modify these values without
# understanding the consequence — they are the appids every
# Release-0.6.1 user has bound to their Steam library state.
# Format: (store, store_game_id) -> expected_appid (signed int32).
_PINNED: dict[tuple[str, str], int] = {
    ("epic",      "1234567890"):                     -709957629,
    ("gog",       "1901494026"):                    -1441049869,
    ("amazon",    "amzn-abc-123"):                   -767669380,
    ("ubisoft",   "5678"):                           -583719067,
    ("microsoft", "BethesdaSoftworks.SkyrimSE-PC"):  -868484378,
}


@pytest.mark.parametrize(("store", "store_game_id"), list(_PINNED.keys()))
def test_matches_v061_oracle(store: str, store_game_id: str) -> None:
    """Current generate_app_id must agree with the v0.6.1 oracle."""
    identity = f"{store}:{store_game_id}"
    # The title argument was unused by v0.6.1's Branch A (game
    # callers always passed a non-protected full_id), so any
    # string is fine here.
    expected = _v061_reference("ignored", _LAUNCHER, identity)
    actual = generate_app_id(_LAUNCHER, identity)
    assert actual == expected, (
        f"generate_app_id drifted from v0.6.1 for {identity!r}: "
        f"got {actual}, oracle says {expected}. "
        f"Every released user's Steam library is keyed on the v0.6.1 "
        f"output — see plan can-you-find-out-wiggly-mccarthy.md."
    )


@pytest.mark.parametrize(("store", "store_game_id", "expected"), [
    (s, gid, h) for (s, gid), h in _PINNED.items()
])
def test_matches_pinned_values(
    store: str, store_game_id: str, expected: int,
) -> None:
    """Current generate_app_id must match the hard-coded reference."""
    identity = f"{store}:{store_game_id}"
    actual = generate_app_id(_LAUNCHER, identity)
    assert actual == expected, (
        f"Pinned hash for {identity!r} no longer matches: got {actual}, "
        f"pinned {expected}. If you intentionally changed the hash "
        f"format, you've broken every Release-0.6.1 user's library."
    )


def test_separator_is_pipe() -> None:
    """The `|` separator between launcher and identity is load-bearing.

    Spelled out as its own test so a future contributor who tries
    to "simplify" the key composition sees the explicit guard
    rather than just two parametrised tests they might not read.
    """
    # If the separator were ever removed, the keys "/path/Unifideck"
    # + "epic:foo" and "/path/Unifideckepic:foo" would collide with
    # any path that happened to end in "Unifideck" and any identity
    # starting with the right suffix. The `|` is the disambiguator.
    with_pipe = generate_app_id("/p/Unifideck", "epic:foo")
    without_pipe_simulated = _crc32_signed("/p/Unifideck" + "epic:foo")
    assert with_pipe != without_pipe_simulated, (
        "generate_app_id no longer includes a `|` separator. This "
        "breaks v0.6.1 backwards compatibility — see the pinning "
        "tests in this module."
    )


def _crc32_signed(key: str) -> int:
    """Helper for the separator guard — CRC32-with-top-bit-set, signed."""
    crc = binascii.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    signed: int = struct.unpack("i", struct.pack("I", crc | 0x80000000))[0]
    return signed
