"""Regression: a failed Amazon install must name nile's real error.

Bug report: four Amazon installs (two different games) failed after ~6s
having transferred 0 bytes. The download history recorded only
``nile_exit_1`` and the UI showed a generic "Failed" — undiagnosable,
because ``_handle_install_line`` sent every non-progress line (which is
exactly where nile prints its error) to a DEBUG logger that the reporter's
log bundle never captured.

Fix mirrors the identical one already made for Epic/legendary: a bounded
ring buffer keeps the last few non-progress lines and folds them into the
error string, preserving the machine-parsable ``nile_exit_{rc}`` prefix
that downstream classification matches on.
"""
from __future__ import annotations

import asyncio
import types

from unifideck.stores.amazon.amazon_install import (
    AmazonInstaller,
    _format_exit_error,
    _RunOutcome,
)
from unifideck.stores.shared.cli_install_helpers import TailRingBuffer


async def _noop_emit(*_args, **_kwargs) -> None:
    """Stand-in for EventBus.emit — progress lines emit DOWNLOAD_PROGRESS."""


def _installer() -> AmazonInstaller:
    return AmazonInstaller(
        bus=types.SimpleNamespace(emit=_noop_emit),
        cli_path="/plugin/bin/nile",
        library=types.SimpleNamespace(),
        find_exe=lambda _p, _h: None,
        default_install_root="/games",
    )


# --------------------------------------------------------------------------
# _format_exit_error
# --------------------------------------------------------------------------
def test_format_exit_error_appends_tail() -> None:
    err = _format_exit_error(_RunOutcome(rc=1, tail="ERROR: Game not found"))
    assert err == "nile_exit_1: ERROR: Game not found"


def test_format_exit_error_bare_when_no_tail() -> None:
    assert _format_exit_error(_RunOutcome(rc=1)) == "nile_exit_1"


def test_format_exit_error_keeps_parsable_prefix() -> None:
    # Downstream classification matches on the prefix — appending the tail
    # must not break it.
    err = _format_exit_error(_RunOutcome(rc=1, tail="whatever"))
    assert err.startswith("nile_exit_1")


# --------------------------------------------------------------------------
# tail buffer wiring: non-progress lines are captured, progress lines aren't
# --------------------------------------------------------------------------
async def test_error_lines_are_captured_progress_lines_are_not() -> None:
    inst = _installer()
    inst._current_progress = {
        "progress_percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0.0,
        "eta_seconds": 0,
    }
    buf = TailRingBuffer()

    for line in (
        "Getting manifest...",
        "= Progress: 42.50 100/200, Running for: 00:00:10, ETA: 00:00:10",
        "ERROR: Unable to fetch game manifest",
    ):
        await inst._handle_install_line(line, "amzn1.adg.product.x", None, tail_buf=buf)

    tail = buf.tail()
    assert "ERROR: Unable to fetch game manifest" in tail
    assert "Getting manifest..." in tail
    # The progress line is consumed for the UI, never treated as error text.
    assert "Progress:" not in tail


async def test_failure_message_carries_the_real_reason() -> None:
    inst = _installer()
    inst._current_progress = {
        "progress_percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0.0,
        "eta_seconds": 0,
    }
    buf = TailRingBuffer()
    await inst._handle_install_line(
        "ERROR: No space left on device", "gid", None, tail_buf=buf,
    )

    err = _format_exit_error(_RunOutcome(rc=1, tail=buf.tail()))

    # The reporter's bundle showed a bare "nile_exit_1"; it must now say why.
    assert err != "nile_exit_1"
    assert "No space left on device" in err


# --------------------------------------------------------------------------
# rc == 0 and no install dir — the stale-manifest no-op
#
# nile prints "Game is up to date" and exits 0 when a stale cached manifest
# makes the download a no-op. Discarding that line left "install_dir_not_found"
# as the only evidence, and the reason had to be recovered from nile's source
# rather than from our own logs.
# --------------------------------------------------------------------------
async def test_finalize_failure_carries_niles_own_words() -> None:
    inst = _installer()

    async def _no_dir(*_args, **_kwargs) -> str | None:
        return None

    inst._resolve_install_path = _no_dir

    res = await inst._finalize_install("gid", "/games", "Game is up to date")

    assert res.success is False
    assert res.error == "install_dir_not_found: Game is up to date"


async def test_finalize_failure_keeps_parsable_prefix_without_a_tail() -> None:
    inst = _installer()

    async def _no_dir(*_args, **_kwargs) -> str | None:
        return None

    inst._resolve_install_path = _no_dir

    res = await inst._finalize_install("gid", "/games", "")

    assert res.error == "install_dir_not_found"


# --------------------------------------------------------------------------
# the spawn itself
# --------------------------------------------------------------------------
def test_format_exit_error_reports_a_spawn_failure() -> None:
    outcome = _RunOutcome(rc=-2, spawn_error="[Errno 13] Permission denied")
    assert _format_exit_error(outcome) == (
        "nile_spawn_failed: [Errno 13] Permission denied"
    )


async def test_unspawnable_nile_is_not_a_generic_unknown_error(monkeypatch) -> None:
    """A lost exec bit on bin/nile must name itself, not escape to the worker."""
    def _refuse(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _refuse)
    inst = _installer()

    outcome = await inst._run_install("/games", "gid", None)

    assert outcome.rc == -2
    assert "Permission denied" in _format_exit_error(outcome)


async def test_nile_is_spawned_with_a_scrubbed_environment(monkeypatch) -> None:
    """The frozen Decky backend leaks its own loader vars to every child.

    Epic got this in the env-sanitization pass and Amazon was missed, so
    ``nile`` still inherited ``LD_LIBRARY_PATH=/tmp/_MEIxxxx`` — the same
    class of leak that made every GOG/Amazon/Ubisoft launch exit 127.
    """
    seen: dict[str, object] = {}

    def _capture(*_args, **kwargs):
        seen.update(kwargs)
        raise OSError("stop here — the env is all this test needs")

    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123456")
    monkeypatch.setenv("PYTHONPATH", "/plugin/py_modules")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)

    await _installer()._run_install("/games", "gid", None)

    env = seen["env"]
    assert isinstance(env, dict)
    assert "LD_LIBRARY_PATH" not in env
    assert "PYTHONPATH" not in env
    # Scrubbed, not emptied — nile still needs PATH and HOME.
    assert env.get("PATH")
