"""Prefix placement for wrapper stores — the shared policy, per store.

A wrapper store's game installs *inside* its Wine prefix, so where the
prefix goes is where the game goes, and Wine reports ``C:``'s free space
from whatever filesystem backs ``drive_c``. Battle.net shipped without
honouring the user's storage pick and the symptom was not a misplaced
folder: the client refused an 83 GB install citing the 45 GB internal drive
while the chosen SD card had 164 GB free.

Every placement assertion here is parametrized over ``WRAPPER_STORES`` on
purpose. Ubisoft and Battle.net had two private copies of this logic and
only one of them worked; iterating the frozenset is what makes the next
wrapper store fail loudly instead of silently repeating the bug.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from unifideck.launcher.wrapper_stores import WRAPPER_STORES
from unifideck.stores.shared.prefix_placement import (
    cleanup_abandoned_prefix,
    prefix_path_for_base,
    reset_for_fresh_install,
    resolve_prefix_target,
)


class _Recorder:
    """A remover that records what it was asked to delete."""

    def __init__(self, *, succeeds: bool = True, raises: bool = False) -> None:
        self.seen: list[Path] = []
        self._succeeds = succeeds
        self._raises = raises

    def __call__(self, path: Path) -> bool:
        self.seen.append(path)
        if self._raises:
            raise OSError("device busy")
        return self._succeeds


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------


@pytest.mark.parametrize("store", sorted(WRAPPER_STORES))
def test_picked_base_becomes_the_prefix_root(store: str) -> None:
    target = resolve_prefix_target(
        store, "42", "/run/media/deck/SD/Games", Path("/internal/default"),
    )
    assert target == Path(f"/run/media/deck/SD/Games/prefixes/{store}/42")


@pytest.mark.parametrize("store", sorted(WRAPPER_STORES))
def test_no_pick_falls_back_to_the_internal_default(store: str) -> None:
    """The fallback is the whole reason a missing pick is not an error."""
    default = Path("/home/deck/.local/share/unifideck/prefixes") / store / "42"
    assert resolve_prefix_target(store, "42", None, default) == default
    assert resolve_prefix_target(store, "42", "", default) == default


def test_a_non_wrapper_store_ignores_the_base() -> None:
    """Their games download outside the prefix, so moving it achieves nothing."""
    default = Path("/internal/prefixes/epic/42")
    assert resolve_prefix_target("epic", "42", "/mnt/sd/Games", default) == default


def test_base_is_user_expanded() -> None:
    target = prefix_path_for_base("~/Games", "ubisoft", "720")
    assert target == Path.home() / "Games" / "prefixes" / "ubisoft" / "720"
    assert "~" not in str(target)


def test_layout_matches_the_one_ubisoft_already_ships() -> None:
    """Pins the on-disk shape live on the dev Deck; detection keys on it."""
    assert prefix_path_for_base("/home/deck/Games", "ubisoft", "46") == Path(
        "/home/deck/Games/prefixes/ubisoft/46",
    )


# --------------------------------------------------------------------------
# fresh-install reset
# --------------------------------------------------------------------------


def test_reset_clears_both_old_and_new_locations(tmp_path: Path) -> None:
    """Changing disks leaves an orphan at the old path unless both go."""
    old = tmp_path / "internal" / "42"
    new = tmp_path / "sd" / "42"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    remover = _Recorder()

    asyncio.run(reset_for_fresh_install(old, new, remover, label="T"))

    assert remover.seen == [old, new]


def test_reset_dedupes_a_reinstall_to_the_same_place(tmp_path: Path) -> None:
    same = tmp_path / "42"
    same.mkdir()
    remover = _Recorder()

    asyncio.run(reset_for_fresh_install(same, same, remover, label="T"))

    assert remover.seen == [same]


def test_reset_skips_paths_that_do_not_exist(tmp_path: Path) -> None:
    remover = _Recorder()

    asyncio.run(
        reset_for_fresh_install(None, tmp_path / "never-made", remover, label="T"),
    )

    assert remover.seen == []


def test_reset_survives_a_remover_that_fails(tmp_path: Path) -> None:
    """Best-effort by design: the caller still clones over the top."""
    target = tmp_path / "42"
    target.mkdir()

    asyncio.run(
        reset_for_fresh_install(None, target, _Recorder(raises=True), label="T"),
    )
    asyncio.run(
        reset_for_fresh_install(None, target, _Recorder(succeeds=False), label="T"),
    )


# --------------------------------------------------------------------------
# abandoned cleanup — the half that must never delete a game
# --------------------------------------------------------------------------


def _cleanup(prefix: Path, *, recorded, holds: bool, remover) -> bool:
    return asyncio.run(
        cleanup_abandoned_prefix(
            prefix,
            recorded=recorded,
            holds_game=lambda _p: holds,
            remover=remover,
            label="T",
        ),
    )


def test_a_prefix_holding_a_game_is_never_deleted(tmp_path: Path) -> None:
    """The one assertion that stands between a failed install and data loss."""
    prefix = tmp_path / "42"
    prefix.mkdir()
    remover = _Recorder()

    assert _cleanup(prefix, recorded=prefix, holds=True, remover=remover) is False
    assert remover.seen == []


def test_an_empty_prefix_at_a_recorded_location_is_reclaimed(tmp_path: Path) -> None:
    prefix = tmp_path / "42"
    prefix.mkdir()
    remover = _Recorder()

    assert _cleanup(prefix, recorded=prefix, holds=False, remover=remover) is True
    assert remover.seen == [prefix]


def test_an_unrecorded_prefix_is_left_alone(tmp_path: Path) -> None:
    """Only the user-picked placement is swept; the internal default is reused."""
    prefix = tmp_path / "42"
    prefix.mkdir()
    remover = _Recorder()

    assert _cleanup(prefix, recorded=None, holds=False, remover=remover) is False
    assert remover.seen == []


def test_a_prefix_that_was_never_created_still_clears_its_record(
    tmp_path: Path,
) -> None:
    """A dangling recorded path is exactly the state that needs clearing.

    Existence is deliberately not a precondition — the removers all treat an
    absent directory as already gone, and returning True is how the caller
    learns to drop the stale id-map entry.
    """
    remover = _Recorder()
    missing = tmp_path / "never-made"

    assert _cleanup(missing, recorded=missing, holds=False, remover=remover) is True


def test_cleanup_reports_failure_when_the_remover_refuses(tmp_path: Path) -> None:
    """A refused delete must not make the caller forget where the prefix is."""
    prefix = tmp_path / "42"
    prefix.mkdir()

    assert _cleanup(
        prefix, recorded=prefix, holds=False, remover=_Recorder(succeeds=False),
    ) is False
    assert _cleanup(
        prefix, recorded=prefix, holds=False, remover=_Recorder(raises=True),
    ) is False
