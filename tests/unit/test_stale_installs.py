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

SECOND ROUND — dropping the installed.json row was not enough
--------------------------------------------------------------
The retry still no-op'd (2.9 s, 0 bytes) and the pruned row came *back*::

    [19:17:08] cleared stale state for amazon:…5d4cab76 — nile installed.json entry
    [19:17:11] cannot locate install directory …

nile keeps a SECOND record — a cached protobuf manifest under
``~/.config/nile/manifests/<id>.raw`` — and that is the one that gates the
download. Per nile v1.1.2's ``downloading/manager.py``,
``load_installed_manifest()`` reads it without consulting installed.json,
``download()`` returns on an empty diff before creating any directory, and
``finish()`` rewrites the row from it. So the manifest must be dropped
whenever the row is not backed by real files — **including when there is no
row at all**, which is precisely the state the first fix left behind.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from unifideck.core import stale_installs as si


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with the CLI record dirs nile and legendary use."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "nile" / "manifests").mkdir(parents=True)
    (tmp_path / ".config" / "legendary").mkdir(parents=True)
    return tmp_path


def _nile(home, entries):
    (home / ".config" / "nile" / "installed.json").write_text(json.dumps(entries))


def _manifest(home, game_id):
    """Plant nile's cached manifest for *game_id* and return its path."""
    p = home / ".config" / "nile" / "manifests" / f"{game_id}.raw"
    p.write_bytes(b"\x08\x01not-really-protobuf")
    return p


def _marked_install(home, store, game_id, name):
    """A real install dir carrying our ownership marker plus a payload file."""
    game_dir = home / "Games" / name
    game_dir.mkdir(parents=True)
    (game_dir / ".unifideck_manifest.json").write_text(
        json.dumps({"store": store, "store_id": game_id}),
    )
    (game_dir / "game.exe").write_text("payload")
    return game_dir


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
    cached = _manifest(home, "real")

    assert si.reconcile_for_install("amazon", "real") == []
    assert len(_read_nile(home)) == 1
    # A live record means a real install: the delta manifest is exactly what
    # nile should keep, so a redundant full re-download is avoided.
    assert cached.is_file()


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


# ── nile's second record: the cached manifest ───────────────────


def test_stale_manifest_is_dropped_when_no_record_row_exists(home):
    """The exact state the first fix left behind.

    Attempt 1 pruned the installed.json row; nile then read its still-cached
    manifest, concluded "Game is up to date", created no directory, exited 0
    and rewrote the row. Without a row to find, the old pruner reported
    "nothing to do" and every retry hit the same wall.
    """
    cached = _manifest(home, "the-gap")

    cleaned = si.reconcile_for_install("amazon", "the-gap")

    assert not cached.exists()
    assert any("manifest" in note for note in cleaned), cleaned


def test_dangling_row_and_manifest_are_both_dropped(home):
    _nile(home, [{"id": "the-gap", "path": str(home / "Games" / "Gone")}])
    cached = _manifest(home, "the-gap")

    cleaned = si.reconcile_for_install("amazon", "the-gap")

    assert _read_nile(home) == []
    assert not cached.exists()
    assert len(cleaned) == 2, cleaned


def test_only_the_requested_games_manifest_is_dropped(home):
    mine = _manifest(home, "target")
    theirs = _manifest(home, "other-game")

    si.reconcile_for_install("amazon", "target")

    assert not mine.exists()
    assert theirs.is_file()


def test_absent_manifest_is_a_noop(home):
    assert si.reconcile_for_install("amazon", "never-installed") == []


def test_manifest_unlink_failure_does_not_block_the_install(home, monkeypatch):
    """Being unable to tidy up is not a reason to refuse to try."""
    _manifest(home, "target")

    def _boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "unlink", _boom)

    assert si.reconcile_for_install("amazon", "target") == []


def test_legendary_path_does_not_touch_nile_state(home):
    """The pruners are per-store; epic must not reach into nile's dirs."""
    cached = _manifest(home, "blob")
    _legendary(home, {"blob": {"install_path": str(home / "Games" / "Gone")}})

    si.reconcile_for_install("epic", "blob")

    assert cached.is_file()


# ── never remove directories ────────────────────────────────────


def test_healthy_install_dir_is_never_removed(home):
    """Regression: an earlier revision swept marked dirs before every install.

    The sweep was not gated on the record being stale, so on a reinstall of a
    perfectly healthy game the two halves disagreed: ``_prune_nile`` correctly
    left the live record alone, and the sweep then ``rmtree``d the directory
    it pointed at. The user lost the install AND landed in the no-op loop this
    module exists to break, because nile's cached manifest still claimed the
    files were there. Directory removal now belongs solely to the uninstall /
    "Delete all data" paths.
    """
    game_dir = _marked_install(home, "amazon", "the-gap", "The Gap")
    _nile(home, [{"id": "the-gap", "path": str(game_dir)}])

    assert si.reconcile_for_install("amazon", "the-gap") == []
    assert (game_dir / "game.exe").read_text() == "payload"


def test_marked_dir_is_not_removed_when_the_record_dangles(home):
    """The other half: a dangling record can mean the game simply moved."""
    game_dir = _marked_install(home, "amazon", "the-gap", "The Gap")
    _nile(home, [{"id": "the-gap", "path": str(home / "Games" / "Gone")}])

    si.reconcile_for_install("amazon", "the-gap")

    assert (game_dir / "game.exe").read_text() == "payload"


def test_marked_dir_survives_for_a_store_with_no_cli_record(home):
    """GOG/Ubisoft have no record to reconcile — and nothing to delete."""
    game_dir = _marked_install(home, "gog", "1207659109", "Some GOG Game")

    assert si.reconcile_for_install("gog", "1207659109") == []
    assert (game_dir / "game.exe").exists()
