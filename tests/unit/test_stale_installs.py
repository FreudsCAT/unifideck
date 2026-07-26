"""Regression: a stale CLI install record must not veto a fresh install.

Field report (Amazon, "The Gap"). The install returned in 1.4 seconds having
downloaded nothing::

    executing: nile install amzn1.adg.product.5d4cab76… --base-path ~/Games
    cannot locate install directory … nile reported success but no
    matching directory found on disk
    failed install for amazon:…: install_dir_not_found

``~/.config/nile/installed.json`` listed FOUR installed games and not one of
their directories existed on disk. nile saw its own entry for the requested
game, concluded there was nothing to do, exited 0 — and the install could
never succeed however many times the user retried, because every retry hit
the same stale record. Only hand-editing nile's state file would break it.

The record outlives the files after a manual delete, a moved SD card, or a
failed "Delete all data". ``amazon_library`` already handles the display side
of this ("nile's installed.json can outlive the directory") so the game does
not show a false PLAY button; nothing reconciled it before an INSTALL.

The reconcile is wired into ``worker._dispatch_install``, the one seam every
store install passes through, because the failure mode is not Amazon's —
legendary keeps the same kind of record.
"""
from __future__ import annotations

import json

import pytest

from unifideck.core import stale_installs as si


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with the two CLI record dirs.

    Patching HOME also isolates the marker sweep for free: it derives its
    install roots from these same record files, so it finds nothing to walk.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "nile").mkdir(parents=True)
    (tmp_path / ".config" / "legendary").mkdir(parents=True)
    return tmp_path


def _nile(home, entries):
    (home / ".config" / "nile" / "installed.json").write_text(json.dumps(entries))


def _legendary(home, entries):
    (home / ".config" / "legendary" / "installed.json").write_text(
        json.dumps(entries),
    )


def _read_nile(home):
    return json.loads((home / ".config" / "nile" / "installed.json").read_text())


def _read_legendary(home):
    return json.loads(
        (home / ".config" / "legendary" / "installed.json").read_text(),
    )


# ── the reported bug ────────────────────────────────────────────


def test_stale_nile_entry_is_pruned(home):
    _nile(home, [{"id": "the-gap", "path": str(home / "Games" / "Gone")}])

    cleaned = si.reconcile_for_install("amazon", "the-gap")

    assert cleaned, "a dangling record must be reported as cleaned"
    assert _read_nile(home) == []


def test_stale_legendary_entry_is_pruned(home):
    _legendary(home, {"blob": {"install_path": str(home / "Games" / "Gone")}})

    cleaned = si.reconcile_for_install("epic", "blob")

    assert cleaned
    assert _read_legendary(home) == {}


# ── the safety direction: never touch a real install ────────────


def test_live_nile_install_is_never_pruned(home):
    real = home / "Games" / "RealGame"
    real.mkdir(parents=True)
    _nile(home, [{"id": "real", "path": str(real)}])

    assert si.reconcile_for_install("amazon", "real") == []
    assert len(_read_nile(home)) == 1


def test_live_legendary_install_is_never_pruned(home):
    real = home / "Games" / "RealGame"
    real.mkdir(parents=True)
    _legendary(home, {"real": {"install_path": str(real)}})

    assert si.reconcile_for_install("epic", "real") == []
    assert "real" in _read_legendary(home)


def test_other_games_records_are_left_alone(home):
    """Only the game being installed is reconciled, stale siblings included.

    Three of the four entries in the field report were also stale. They are
    deliberately left for their own install to clear — a pre-install hook has
    no business rewriting records for games the user did not ask about.
    """
    _nile(home, [
        {"id": "target", "path": str(home / "Games" / "GoneA")},
        {"id": "other-stale", "path": str(home / "Games" / "GoneB")},
    ])

    si.reconcile_for_install("amazon", "target")

    assert [e["id"] for e in _read_nile(home)] == ["other-stale"]


# ── shapes, absences, and junk ──────────────────────────────────


def test_entry_with_no_recorded_path_is_pruned(home):
    """A record claiming an install with nowhere to point is just as unusable."""
    _legendary(home, {"blob": {}})

    assert si.reconcile_for_install("epic", "blob")
    assert _read_legendary(home) == {}


def test_game_absent_from_the_record_is_a_noop(home):
    _nile(home, [{"id": "someone-else", "path": str(home)}])

    assert si.reconcile_for_install("amazon", "not-recorded") == []
    assert len(_read_nile(home)) == 1


def test_missing_record_file_is_a_noop(home):
    assert si.reconcile_for_install("amazon", "anything") == []
    assert si.reconcile_for_install("epic", "anything") == []


def test_corrupt_record_file_is_a_noop(home):
    (home / ".config" / "nile" / "installed.json").write_text("{not json")

    assert si.reconcile_for_install("amazon", "anything") == []


def test_unexpected_record_shape_is_a_noop(home):
    """nile writes a list and legendary a dict — never assume either."""
    _nile(home, {"unexpected": "dict"})
    _legendary(home, ["unexpected", "list"])

    assert si.reconcile_for_install("amazon", "x") == []
    assert si.reconcile_for_install("epic", "x") == []


def test_non_dict_entries_are_preserved(home):
    """Junk in the record is passed through, not silently dropped."""
    _nile(home, ["junk", {"id": "target", "path": str(home / "Gone")}])

    si.reconcile_for_install("amazon", "target")

    assert _read_nile(home) == ["junk"]


def test_store_without_a_cli_record_is_a_noop(home):
    """GOG and Ubisoft keep no CLI-side install record."""
    assert si.reconcile_for_install("gog", "1207659109") == []
    assert si.reconcile_for_install("ubisoft", "720") == []


def test_rewrite_is_atomic_and_leaves_no_temp_file(home):
    _nile(home, [{"id": "target", "path": str(home / "Gone")}])

    si.reconcile_for_install("amazon", "target")

    leftovers = list((home / ".config" / "nile").glob("*.tmp"))
    assert leftovers == [], f"temp file left behind: {leftovers}"
