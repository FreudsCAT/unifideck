"""The Ubisoft template clone must not revert the prefix's Proton marker.

``_clone_template_into`` is last-resort recovery: when a game prefix has no
upc.exe, the prebuilt ``.template`` is rsync'd over it. The template ships its
own ``.unifideck_proton_version`` (written when it was built), and ``rsync -a``
preserves mtime — so the copy silently restored a stale marker over whatever
the current launch had just stamped.

That turned a one-off prefix reset into a permanent loop: every launch read the
template's ``Proton - Experimental``, resolved something else, declared a
family change, reset the prefix (deleting the installed game), re-cloned, and
reverted the marker again. Confirmed on-device 2026-08-01 — the marker in the
live Rayman Origins prefix was byte- and nanosecond-identical to the
template's.

``subprocess.run`` is faked rather than shelling out, so both copy branches
(rsync, and the ``cp -a`` fallback) are exercised deterministically without
depending on either binary being installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from unifideck.launcher.proton.compat.prefix_init import _MARKER_NAME
from unifideck.launcher.proton.handlers import ubisoft_recovery as ubisoft

UPC_REL = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"


@pytest.fixture
def template(tmp_path, monkeypatch):
    """A minimal ``.template`` carrying its own (stale) Proton marker."""
    root = tmp_path / "template"
    upc = root / UPC_REL
    upc.parent.mkdir(parents=True)
    upc.write_text("upc")
    (root / _MARKER_NAME).write_text("Proton - Experimental")
    monkeypatch.setattr(ubisoft, "_TEMPLATE_DIR", root)
    return root


@pytest.fixture(params=["rsync", "cp-fallback"])
def copier(request, monkeypatch):
    """Fake ``rsync``/``cp`` that really copies, marker included.

    Copying the marker is the point: the production tools do it, so the code
    under test has to put the target's own marker back afterwards.
    """
    mode = request.param

    def _fake_run(argv, **kwargs):
        tool = argv[0]
        if tool == "rsync" and mode == "cp-fallback":
            return subprocess.CompletedProcess(argv, 1, b"", b"failed")
        # rsync gets ``<src>/``; cp gets ``<src>/.`` — Path normalises both to
        # the template dir itself.
        shutil.copytree(
            Path(argv[-2]), Path(argv[-1]), dirs_exist_ok=True, symlinks=True,
        )
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(ubisoft.subprocess, "run", _fake_run)
    return mode


def test_clone_keeps_the_targets_marker(template, copier, tmp_path):
    """The marker the current launch stamped survives the clone."""
    target = tmp_path / "80"
    target.mkdir()
    (target / _MARKER_NAME).write_text("GE-Proton11-3")

    assert ubisoft.clone_template_into(target) is True

    # UPC landed (the point of the clone)...
    assert (target / UPC_REL).is_file()
    # ...without the template's marker overwriting ours.
    assert (target / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-3"


def test_clone_into_fresh_dir_takes_the_template_marker(template, copier, tmp_path):
    """No marker to preserve → the template's is fine (nothing to revert)."""
    target = tmp_path / "81"

    assert ubisoft.clone_template_into(target) is True
    assert (
        (target / _MARKER_NAME).read_text(encoding="utf-8") == "Proton - Experimental"
    )


def test_clone_injects_credentials(template, copier, tmp_path, monkeypatch):
    """A cloned prefix must be SIGNED IN, not just have upc.exe.

    ``.template`` is deliberately pristine — its ConnectSecureStorage.dat is
    the never-logged-in shape and it has no user.dat — so a bare clone hands
    the user a UPC that demands a sign-in they already completed. The install
    path injects via ``bootstrap_game_prefix``; this recovery never did.
    """
    injected: list[str] = []
    monkeypatch.setattr(
        ubisoft, "_inject_credentials", lambda p: injected.append(str(p)),
    )
    target = tmp_path / "80"

    assert ubisoft.clone_template_into(target) is True
    assert injected == [str(target)]


def test_no_injection_when_the_clone_failed(template, tmp_path, monkeypatch):
    """Nothing to sign into if upc.exe never landed."""
    injected: list[str] = []
    monkeypatch.setattr(
        ubisoft, "_inject_credentials", lambda p: injected.append(str(p)),
    )
    monkeypatch.setattr(
        ubisoft.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, b"", b""),
    )

    assert ubisoft.clone_template_into(tmp_path / "83") is False
    assert injected == []


def test_injection_failure_never_breaks_the_launch(tmp_path, monkeypatch, caplog):
    """Best-effort: a failing session facade is logged, never raised.

    The launch must still proceed — the user gets the sign-in prompt, which is
    exactly the pre-fix behaviour, rather than a dead launch.
    """
    import unifideck.stores.ubisoft.session as session_pkg

    def _boom() -> None:
        raise RuntimeError("no config manager")

    monkeypatch.setattr(session_pkg, "build_standalone_session", _boom)

    ubisoft._inject_credentials(tmp_path / "80")  # must not raise

    assert "credential injection into" in caplog.text


def test_clone_is_a_noop_when_upc_already_present(template, tmp_path):
    """Guard: an already-populated prefix is never overwritten."""
    target = tmp_path / "82"
    (target / UPC_REL).parent.mkdir(parents=True)
    (target / UPC_REL).write_text("existing upc")
    (target / _MARKER_NAME).write_text("GE-Proton11-3")

    assert ubisoft.clone_template_into(target) is True

    assert (target / UPC_REL).read_text() == "existing upc"
    assert (target / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-3"


def test_restore_marker_tolerates_an_unwritable_prefix(tmp_path, caplog):
    """A failed restore logs and returns — it must never break the launch."""
    missing = tmp_path / "does" / "not" / "exist"

    ubisoft._restore_proton_marker(missing, "GE-Proton11-3")  # must not raise

    assert "could not restore the Proton marker" in caplog.text


def test_restore_marker_is_a_noop_without_one(tmp_path):
    ubisoft._restore_proton_marker(tmp_path, None)
    assert not (tmp_path / _MARKER_NAME).exists()


def test_read_marker_returns_none_when_absent(tmp_path):
    assert ubisoft._read_proton_marker(tmp_path) is None
