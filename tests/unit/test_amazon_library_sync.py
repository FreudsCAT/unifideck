"""Amazon library re-fetch before read (UD-012).

Regression guard for "newly-claimed Amazon Prime Gaming games never
appear after sync". The Amazon store used to only *read* nile's on-disk
``library.json``, which nile only (re)writes at login/register — so games
claimed after the last login never entered the file and never showed up,
no matter how many Force Syncs. The fix runs ``nile library sync`` before
reading (every sync, parity with Epic/GOG), gated on auth, best-effort:

* ``AmazonLibraryReader.sync_library`` shells out to ``nile library sync``,
  targeting the reader's config dir via ``XDG_CONFIG_HOME``, and never
  raises — it returns ``False`` on any failure so the caller falls through
  to the last-known file.
* ``AmazonStore.get_library`` calls ``sync_library`` before the read when
  authed, and still returns the cached library if the refresh fails.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from unifideck.core.types import Game
from unifideck.stores.amazon.amazon_library import AmazonLibraryReader
from unifideck.stores.amazon.amazon_store import AmazonStore

def _default_nile_dir() -> str:
    """nile's ambient default config dir, resolved against the CURRENT HOME.

    Must be computed inside the test, not at import: the suite redirects
    ``HOME`` per-test, so a module-level constant would bake in the real
    home and no longer match what the code under test resolves.
    """
    return str(Path("~/.config/nile").expanduser())


def _reader(config_dir: str) -> AmazonLibraryReader:
    return AmazonLibraryReader(config_dir=config_dir)


def _proc(returncode: int, stderr: bytes = b"") -> AsyncMock:
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate.return_value = (b"", stderr)
    return proc


# ── AmazonLibraryReader.sync_library ──────────────────────────────


async def test_sync_library_runs_correct_nile_args(tmp_path):
    """Success path: runs ``nile library sync`` and returns True."""
    reader = _reader(str(tmp_path / "nile"))
    with patch(
        "asyncio.create_subprocess_exec", return_value=_proc(0),
    ) as mock_exec:
        ok = await reader.sync_library("/plugin/bin/nile", 60)

    assert ok is True
    assert mock_exec.call_args.args == ("/plugin/bin/nile", "library", "sync")


async def test_sync_library_adds_no_xdg_override_for_default_dir(monkeypatch):
    """Config dir == ambient default → nile's own default is left to stand."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    reader = _reader(_default_nile_dir())
    with patch(
        "asyncio.create_subprocess_exec", return_value=_proc(0),
    ) as mock_exec:
        await reader.sync_library("/plugin/bin/nile", 60)

    assert "XDG_CONFIG_HOME" not in mock_exec.call_args.kwargs["env"]


async def test_sync_library_targets_config_dir_via_xdg(tmp_path, monkeypatch):
    """Non-default ``.../nile`` dir → XDG_CONFIG_HOME points at its parent."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    custom = tmp_path / "custom" / "nile"
    reader = _reader(str(custom))
    with patch(
        "asyncio.create_subprocess_exec", return_value=_proc(0),
    ) as mock_exec:
        await reader.sync_library("/plugin/bin/nile", 60)

    env = mock_exec.call_args.kwargs["env"]
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "custom")


async def test_sync_library_scrubs_the_frozen_loaders_env(tmp_path, monkeypatch):
    """The env is never inherited verbatim, in either config-dir branch.

    The Decky backend is PyInstaller-frozen, so its ``os.environ`` carries
    ``LD_LIBRARY_PATH=/tmp/_MEIxxxx``. Passing that down makes the child link
    the loader's libraries — the leak that made every GOG/Amazon/Ubisoft
    launch exit 127. Epic got the scrubber; Amazon was missed.
    """
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123456")
    monkeypatch.setenv("PYTHONPATH", "/plugin/py_modules")
    for config_dir in (_default_nile_dir(), str(tmp_path / "custom" / "nile")):
        reader = _reader(config_dir)
        with patch(
            "asyncio.create_subprocess_exec", return_value=_proc(0),
        ) as mock_exec:
            await reader.sync_library("/plugin/bin/nile", 60)

        env = mock_exec.call_args.kwargs["env"]
        assert "LD_LIBRARY_PATH" not in env, config_dir
        assert "PYTHONPATH" not in env, config_dir
        assert env.get("PATH"), config_dir


async def test_sync_library_returns_false_on_nonzero_rc(tmp_path):
    """A failed sync (e.g. auth error) returns False, never raises."""
    reader = _reader(str(tmp_path / "nile"))
    with patch(
        "asyncio.create_subprocess_exec",
        return_value=_proc(1, b"User not logged in"),
    ):
        assert await reader.sync_library("/plugin/bin/nile", 60) is False


async def test_sync_library_returns_false_on_timeout(tmp_path):
    """A hung sync times out and returns False, never raises."""
    reader = _reader(str(tmp_path / "nile"))
    with patch(
        "asyncio.create_subprocess_exec", side_effect=TimeoutError,
    ):
        assert await reader.sync_library("/plugin/bin/nile", 60) is False


async def test_sync_library_returns_false_on_oserror(tmp_path):
    """A missing/broken binary (OSError) returns False, never raises."""
    reader = _reader(str(tmp_path / "nile"))
    with patch(
        "asyncio.create_subprocess_exec", side_effect=OSError("nope"),
    ):
        assert await reader.sync_library("/plugin/bin/nile", 60) is False


# ── AmazonStore.get_library orchestration ─────────────────────────


def _store(*, authed: bool, sync_ok: bool = True) -> AmazonStore:
    """A bare AmazonStore with only the bits ``get_library`` touches."""
    store = object.__new__(AmazonStore)
    store.cli_path = "/plugin/bin/nile"
    store._amazon_cfg = {"library_sync_timeout_seconds": 60}
    store._library = MagicMock()
    store._library.sync_library = AsyncMock(return_value=sync_ok)
    store._library.read_installed_ids = AsyncMock(return_value={})
    store._check_nile_authenticated = MagicMock(return_value=authed)
    return store


async def test_get_library_syncs_before_read():
    """The refresh must run BEFORE the owned-games read."""
    store = _store(authed=True)
    order: list[str] = []
    store._library.sync_library = AsyncMock(
        side_effect=lambda *a, **k: order.append("sync"),
    )
    store._library.read_owned_games = AsyncMock(
        side_effect=lambda: order.append("read") or [],
    )

    await store.get_library()

    assert order == ["sync", "read"]


async def test_get_library_skips_sync_when_unauthenticated():
    """No auth → no sync attempt, but the file read still runs."""
    store = _store(authed=False)
    store._library.read_owned_games = AsyncMock(return_value=[])

    await store.get_library()

    store._library.sync_library.assert_not_awaited()
    store._library.read_owned_games.assert_awaited_once()


async def test_get_library_returns_cached_library_when_sync_fails():
    """A failed refresh still yields the last-known library, not []."""
    store = _store(authed=True, sync_ok=False)
    owned = [
        Game(app_id=0, store="amazon", store_game_id="g1", title="G1"),
    ]
    store._library.read_owned_games = AsyncMock(return_value=owned)

    result = await store.get_library()

    assert result is not None
    assert [g.store_game_id for g in result] == ["g1"]
