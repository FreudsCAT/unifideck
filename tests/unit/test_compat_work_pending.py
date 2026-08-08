"""``compat_work_pending`` + the VC++ marker keyed to Proton's prefix stamp.

Both exist to stop a prefix and its launch Proton disagreeing.

``setup_prefix`` reroutes to managed GE-Proton whenever the selected Proton
can't run umu's winetricks verb. Firing that on an already-warmed prefix pulled
EVERY launch onto a different Proton than the game would otherwise use, and each
switch makes Proton re-run ``wineboot -u`` and rewrite ``system.reg``
("Upgrading prefix from X to Y" / "Prefix has an invalid version?!"), erasing the
VC++ keys ``compat.vcruntime`` had just imported. Gating the reroute on there
actually being work left is what keeps a warmed prefix untouched.

The vcruntime marker used to be keyed to OUR Proton tool id, which cannot see
the case that erases the keys — some *other* Proton rewriting the prefix. It is
now keyed to Proton's own ``version`` stamp, so any rewrite re-triggers it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from unifideck.launcher.proton.compat import compat_work_pending, vcruntime


def _plan(prefix: Path, *, store: str = "gog", tool: str = "GE-Proton11-3"):
    return SimpleNamespace(
        prefix_path=prefix,
        context=SimpleNamespace(store=store),
        state=SimpleNamespace(proton_tool_id=tool),
    )


def _warmed(prefix: Path, *, stamp: str = "GE-Proton11-3") -> None:
    """A prefix with both compat steps already terminal."""
    prefix.mkdir(parents=True, exist_ok=True)
    (prefix / "system.reg").write_text("", encoding="utf-8")
    (prefix / "version").write_text(stamp + "\n", encoding="utf-8")
    (prefix / "unifideck_winetricks_complete.marker").write_text(
        "complete", encoding="utf-8",
    )
    (prefix / vcruntime._MARKER_NAME).write_text(stamp, encoding="utf-8")


def test_uninitialised_prefix_is_pending(tmp_path):
    assert compat_work_pending(_plan(tmp_path / "prefix")) is True


def test_fully_warmed_prefix_is_not_pending(tmp_path):
    prefix = tmp_path / "prefix"
    _warmed(prefix)
    assert compat_work_pending(_plan(prefix)) is False


def test_ubisoft_is_never_pending(tmp_path):
    # Ubisoft games get their redistributables from UPC; generic compat is
    # skipped entirely, so it must never justify a Proton reroute either.
    assert compat_work_pending(_plan(tmp_path / "prefix", store="ubisoft")) is False


def test_incomplete_winetricks_is_pending(tmp_path):
    prefix = tmp_path / "prefix"
    _warmed(prefix)
    (prefix / "unifideck_winetricks_complete.marker").unlink()
    assert compat_work_pending(_plan(prefix)) is True


def test_foreign_proton_rewriting_the_prefix_makes_vcreg_pending(tmp_path):
    """The regression the old per-tool marker could not see.

    Another Proton ran ``wineboot -u`` and re-stamped the prefix, wiping the
    imported keys. Our marker still named our own tool, so the import was
    skipped forever and the prefix stayed permanently without the keys.
    """
    prefix = tmp_path / "prefix"
    _warmed(prefix, stamp="GE-Proton11-3")
    assert compat_work_pending(_plan(prefix)) is False

    # Proton-Experimental launches the game and upgrades the prefix.
    (prefix / "version").write_text("11.0-100\n", encoding="utf-8")

    assert vcruntime.vcruntime_fix_pending(_plan(prefix)) is True
    assert compat_work_pending(_plan(prefix)) is True


def test_vcreg_pending_when_never_imported(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "version").write_text("GE-Proton11-3", encoding="utf-8")
    assert vcruntime.vcruntime_fix_pending(_plan(prefix)) is True
