"""Finding a wrapper store's vendor clients across prefixes.

Two clients of the same store running at once is a session-loss bug, not an
inconvenience: every prefix is a clone, so both present the same client
instance id and the same token, both refresh it, and the server invalidates
one. The user reaches it by opening the Sign-In tile and then launching a
game.

The ``/proc`` reads are faked here. That is deliberate rather than a
shortcut — the real thing depends on live Wine processes, and the logic worth
pinning is the matching rules, each of which was earned by a measured
misidentification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.launcher.proton.handlers import wrapper_clients as wc


class _FakeProc:
    """A ``/proc`` stand-in: pid -> (cmdline, WINEPREFIX)."""

    def __init__(self, table: dict[str, tuple[str, str | None]]) -> None:
        self._table = table

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wc, "pids", lambda: list(self._table))
        monkeypatch.setattr(
            wc, "proc_field",
            lambda pid, field: self._field(pid, field),
        )

    def _field(self, pid: str, field: str) -> str:
        cmdline, prefix = self._table.get(pid, ("", None))
        if field == "cmdline":
            return cmdline
        if field == "environ":
            entries = ["PATH=/usr/bin"]
            if prefix is not None:
                entries.append(f"WINEPREFIX={prefix}")
            return "\x00".join(entries)
        return ""


# --------------------------------------------------------------------------
# the matching rules
# --------------------------------------------------------------------------


def test_image_name_strips_the_windows_path() -> None:
    cmdline = "C:\\Program Files (x86)\\Battle.net\\Battle.net.exe\x00--type=renderer"
    assert wc.image_name(cmdline) == "battle.net.exe"


def test_wineprefix_is_read_as_an_exact_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A substring test would match STEAM_COMPAT_DATA_PATH and PROTONPATH.

    Both carry the same path, so ``"WINEPREFIX" in environ`` finds the string
    while the value belongs to a different variable.
    """
    monkeypatch.setattr(
        wc, "proc_field",
        lambda _pid, _field: "\x00".join([
            f"STEAM_COMPAT_DATA_PATH={tmp_path}/other",
            f"WINEPREFIX={tmp_path}/real",
        ]),
    )
    assert wc.wineprefix_of("1") == wc.normalise_prefix(tmp_path / "real")


def test_normalise_prefix_collapses_the_umu_self_symlink(tmp_path: Path) -> None:
    """umu rewrites WINEPREFIX to ``<prefix>/pfx`` and makes ``pfx -> .``."""
    prefix = tmp_path / "game"
    prefix.mkdir()
    (prefix / "pfx").symlink_to(".")
    assert wc.normalise_prefix(prefix / "pfx") == wc.normalise_prefix(prefix)


def test_scan_prefix_ignores_the_linux_umu_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """WINEPREFIX is inherited by srt-bwrap, pv-adverb, umu-run and python3.

    Measured on-device: a phase-C ``srt-bwrap`` was reported as "game process
    appeared after 0s", defeating the silent-failure detector the scan exists
    to feed.
    """
    prefix = str(tmp_path / "game")
    _FakeProc({
        "1": ("/usr/bin/srt-bwrap\x00--args", prefix),
        "2": ("/usr/bin/python3\x00umu-run", prefix),
        "3": ("Z:\\game\\Diablo IV.exe", prefix),
    }).install(monkeypatch)

    assert [image for _, image, _ in wc.scan_prefix(prefix)] == ["diablo iv.exe"]


# --------------------------------------------------------------------------
# the cross-prefix question
# --------------------------------------------------------------------------


def test_live_client_prefixes_finds_a_client_in_another_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    auth = str(tmp_path / "auth")
    other = str(tmp_path / "other")
    _FakeProc({
        "1": ("C:\\Battle.net\\Battle.net.exe", auth),
        "2": ("C:\\Battle.net\\Battle.net.exe\x00--type=gpu-process", other),
    }).install(monkeypatch)

    found = {p.name for p in wc.live_client_prefixes("battlenet")}
    assert found == {"auth", "other"}


def test_live_client_prefixes_honours_exclude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The prefix we are about to start in is not "somewhere else"."""
    mine = tmp_path / "mine"
    mine.mkdir()
    _FakeProc({"1": ("C:\\Battle.net\\Battle.net.exe", str(mine))}).install(monkeypatch)

    assert wc.live_client_prefixes("battlenet", exclude=(mine,)) == []


def test_live_client_prefixes_ignores_games_and_infrastructure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A running *game* must not read as a running client.

    Otherwise launching a second title while the first is being played would
    be refused, which is a legitimate thing to do.
    """
    prefix = str(tmp_path / "playing")
    _FakeProc({
        "1": ("Z:\\game\\Diablo IV.exe", prefix),
        "2": ("C:\\windows\\system32\\services.exe", prefix),
    }).install(monkeypatch)

    assert wc.live_client_prefixes("battlenet") == []


def test_processes_without_a_wineprefix_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeProc({"1": ("/usr/bin/bash", None)}).install(monkeypatch)
    assert wc.live_client_prefixes("battlenet") == []


def test_a_store_with_no_client_images_reports_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Non-wrapper stores have no vendor client, so the question is empty."""
    _FakeProc({
        "1": ("C:\\Battle.net\\Battle.net.exe", str(tmp_path / "a")),
    }).install(monkeypatch)
    assert wc.live_client_prefixes("epic") == []
    assert wc.client_running_in("epic", tmp_path / "a") is False


def test_client_running_in_counts_the_launcher_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Deliberately a superset of the readiness probe.

    Callers use it to decide when a client has *fully* exited and its rotated
    token is safe to read; over-reporting liveness costs a short wait, while
    under-reporting reads a torn vault.
    """
    prefix = tmp_path / "game"
    _FakeProc({
        "1": ("C:\\Battle.net\\Battle.net Launcher.exe", str(prefix)),
    }).install(monkeypatch)

    assert wc.client_running_in("battlenet", prefix) is True


def test_battlenet_watch_and_the_scan_agree_on_the_client_images() -> None:
    """One definition of "the client", so teardown and the guard cannot drift.

    ``wrapper_stores`` exists because the same question asked separately in
    five places is how a prefix holding a real game got deleted.
    """
    from unifideck.launcher.proton.handlers import battlenet_watch as watch

    assert watch._CLIENT_IMAGES is wc.CLIENT_IMAGES["battlenet"]
