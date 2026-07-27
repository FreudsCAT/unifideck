"""Tests for the path audit and the derived sanity checks.

The audit is the part of the bundle a support engineer reads first, so
these tests are mostly about it telling the truth: every registry row
accounted for, credentials reported without being read, and "missing"
distinguished from "we were never told where to look".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from unifideck.services.support_bundle import checks, path_audit, resolve
from unifideck.services.support_bundle.sources import COLLECTED
from unifideck.services.support_bundle.sources_audit import all_sources
from unifideck.services.support_bundle.spec import BundleContext

SENTINEL = "SUPERSECRET-DO-NOT-SHIP"


class _FakeConfig:
    def get_str(self, key: str, default: str = "") -> str:
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        return default

    def get(self, key: str, default: Any = None) -> Any:
        return default


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _Paths:
    """ServicePaths stand-in with every field the audit reads."""

    def __init__(self, home: Path) -> None:
        data = home / ".local" / "share" / "unifideck"
        steam = home / ".local" / "share" / "Steam"
        self.data_dir = str(data)
        self.plugin_dir = str(home / "homebrew" / "plugins" / "Unifideck")
        self.steam_root = str(steam)
        self.launcher_path = f"{self.plugin_dir}/bin/unifideck-launcher"
        self.shortcuts_path = str(steam / "userdata/1/config/shortcuts.vdf")
        self.games_map_path = f"{data}/games.map"
        self.playtime_db = f"{data}/playtime.db"
        self.grid_dir = str(steam / "userdata/1/config/grid")
        self.config_vdf_path = str(steam / "userdata/1/config/localconfig.vdf")
        self.loginusers_path = str(steam / "config/loginusers.vdf")


def _ctx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BundleContext:
    """Build a context over a fake home."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("DECKY_PLUGIN_LOG_DIR", "DECKY_HOME", "DECKY_PLUGIN_DIR"):
        monkeypatch.delenv(name, raising=False)
    paths = _Paths(home)
    roots, sources, _ = resolve.build_roots(_FakeConfig(), paths)
    return BundleContext(
        roots=roots, root_sources=sources, config=_FakeConfig(), paths=paths,
    )


# ── coverage of the registry ───────────────────────────────────────
def test_every_registry_row_is_audited_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Nothing in the registry may be silently skipped."""
    ctx = _ctx(monkeypatch, tmp_path)
    records = path_audit.audit_paths(ctx)
    keys = [record.key for record in records]
    assert len(keys) == len(set(keys)), "a row was audited twice"
    expected = {spec.key for spec in all_sources() if spec.root != "generated"}
    assert set(keys) == expected


def test_registry_never_globs_a_whole_directory() -> None:
    """The structural guarantee behind the whole feature.

    The registry is an allowlist. A wildcard against the data or config
    directory would let a future change drop a new secret in and have it
    swept into every user's bundle with no visible diff.
    """
    for spec in all_sources():
        if spec.root not in ("data", "config"):
            continue
        assert spec.pattern not in ("*", "**", "*.*"), spec.key
        assert not spec.pattern.startswith("*"), spec.key


def test_collected_rows_declare_an_archive_directory() -> None:
    for spec in COLLECTED:
        assert spec.arch_dir, f"{spec.key} would land at the archive root"


# ── individual row semantics ───────────────────────────────────────
def test_present_file_reports_size_mode_and_mtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ctx = _ctx(monkeypatch, tmp_path)
    _write(Path(ctx.roots["data"] or "") / "settings.json", '{"locale": "en"}')
    record = _row(path_audit.audit_paths(ctx), "settings")
    assert record.status == "present"
    assert record.size == 16
    assert record.mode
    assert record.mtime is not None


def test_missing_row_carries_expectation_and_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A MISSING line is only actionable with these two fields.

    ``expect`` says whether absence is suspicious; ``writer`` says which
    code should have created it.
    """
    ctx = _ctx(monkeypatch, tmp_path)
    record = _row(path_audit.audit_paths(ctx), "gog_token")
    assert record.status == "missing"
    assert record.expect == "gog"
    assert "account_manager" in record.writer


def test_credentials_are_stat_only_never_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The audit must report the file without reading a byte of it."""
    ctx = _ctx(monkeypatch, tmp_path)
    _write(Path(ctx.roots["config"] or "") / "gog_token.json", SENTINEL)
    opened: list[str] = []
    real_open = Path.open

    def _tracking_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _tracking_open)
    record = _row(path_audit.audit_paths(ctx), "gog_token")
    assert record.status == "present"
    assert record.action == "presence_only"
    assert not any("gog_token" in item for item in opened)


def test_unset_service_path_is_not_reported_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """"We were never told where this is" is not "it is not there".

    Conflating them would blame the user's device for a wiring gap.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    roots, sources, _ = resolve.build_roots(_FakeConfig(), None)
    ctx = BundleContext(roots=roots, root_sources=sources, paths=None)
    record = _row(path_audit.audit_paths(ctx), "shortcuts_vdf")
    assert record.status == "root_unresolved"
    assert record.action == "skipped"


def test_bulk_directory_is_counted_not_walked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Prefix directories are tens of GB; the audit stays shallow."""
    ctx = _ctx(monkeypatch, tmp_path)
    prefixes = Path(ctx.roots["data"] or "") / "prefixes"
    for index in range(3):
        _write(prefixes / f"game{index}" / "pfx" / "user.reg", "deep")
    record = _row(path_audit.audit_paths(ctx), "prefixes")
    assert record.status == "present_dir"
    assert record.entries == 3
    assert record.action == "excluded_bulk"


def test_glob_row_collapses_to_one_summary_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ctx = _ctx(monkeypatch, tmp_path)
    launches = Path(ctx.roots["launches"] or "")
    for name in ("a.log", "b.log", "c.log"):
        _write(launches / name, "x" * 10)
    record = _row(path_audit.audit_paths(ctx), "launch_python_logs")
    assert record.status == "present(3)"
    assert record.size == 30


def test_resolved_via_names_the_winning_resolver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ctx = _ctx(monkeypatch, tmp_path)
    record = _row(path_audit.audit_paths(ctx), "shortcuts_vdf")
    assert record.resolved_via == "ServicePaths.shortcuts_path"


def test_unexpected_missing_ignores_conditional_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A user with no Amazon account must not look broken."""
    ctx = _ctx(monkeypatch, tmp_path)
    records = path_audit.audit_paths(ctx)
    flagged = {record.key for record in path_audit.unexpected_missing(records)}
    for conditional in ("nile_user", "gog_token", "ubisoft_id_map"):
        assert conditional not in flagged


def _row(records: list[Any], key: str) -> Any:
    """Find one audit record by key."""
    return next(record for record in records if record.key == key)


# ── the checks ─────────────────────────────────────────────────────
def _run_checks(ctx: BundleContext, env: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run every check and index the verdicts by name."""
    records = path_audit.audit_paths(ctx)
    results = checks.run_checks(ctx, records, env or {})
    return {item.name: item for item in results}


def test_triangulation_flags_an_empty_shortcuts_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The unambiguous symptom: state recorded, Steam database empty.

    This is what "I synced and no games appeared" looks like from the
    outside, and it currently takes three separate file requests to
    establish.
    """
    ctx = _ctx(monkeypatch, tmp_path)
    data = Path(ctx.roots["data"] or "")
    _write(data / "shortcuts_registry.json", '{"epic:a": {}, "gog:b": {}}')
    shortcuts = Path(str(ctx.paths.shortcuts_path))
    shortcuts.parent.mkdir(parents=True, exist_ok=True)
    shortcuts.write_bytes(b"\x00shortcuts\x00\x08\x08")
    verdict = _run_checks(ctx)["shortcut_count_triangulation"]
    assert verdict.status == "fail"
    assert "synced but no games appear" in verdict.detail


def test_triangulation_tolerates_a_larger_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Regression: this used to fail on a perfectly healthy device.

    The registry is keyed by ``store:game_id`` and remembers every game
    ever synced, so it legitimately holds more entries than the live
    shortcut database. Asserting equality produced a red FAIL on a
    working install, which costs more trust than the check is worth.
    """
    ctx = _ctx(monkeypatch, tmp_path)
    data = Path(ctx.roots["data"] or "")
    _write(
        data / "shortcuts_registry.json",
        '{"epic:a": {}, "gog:b": {}, "ubisoft:c": {}}',
    )
    _write(data / "games.map", "# comment header\n\nepic:a=/x\t/y\t123\n")
    shortcuts = Path(str(ctx.paths.shortcuts_path))
    shortcuts.parent.mkdir(parents=True, exist_ok=True)
    shortcuts.write_bytes(b"\x02appid\x00\x01\x00\x00\x00")
    verdict = _run_checks(ctx)["shortcut_count_triangulation"]
    assert verdict.status == "pass"
    assert "not expected to match" in verdict.detail


def test_games_map_comment_header_is_not_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """games.map ships a two-line comment header describing itself."""
    ctx = _ctx(monkeypatch, tmp_path)
    _write(
        Path(ctx.roots["data"] or "") / "games.map",
        "# Unifideck non-Steam shortcut manifest\n"
        "# Format: store:game_id=exe\\twork_dir\\tapp_id\n"
        "epic:a=/x\t/y\t123\n",
    )
    records = path_audit.audit_paths(ctx)
    by_key = {record.key: record for record in records}
    from unifideck.services.support_bundle import counts

    assert counts.text_lines(by_key["games_map"]) == 1


def test_missing_launcher_binary_fails_but_unresolved_is_na(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A harness with no plugin dir must not look like a broken install."""
    ctx = _ctx(monkeypatch, tmp_path)
    unresolved = _run_checks(ctx, {"plugin": {"resolved": False}})
    assert unresolved["launcher_binary_executable"].status == "na"
    env = {
        "plugin": {
            "resolved": True,
            "binaries": {"unifideck-launcher": {"present": False}},
        },
    }
    assert _run_checks(ctx, env)["launcher_binary_executable"].status == "fail"


def test_storage_visibility_ignores_internal_bind_mounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Regression: this check used to fail on every healthy device.

    SteamOS bind-mounts the internal disk at paths the plugin's
    install-target scanner filters out, so comparing every mount marked
    a working machine as broken.
    """
    ctx = _ctx(monkeypatch, tmp_path)
    env = {
        "storage": {
            "devices": [
                {
                    "name": "nvme0n1p8", "class": "internal",
                    "mounted_at": "/var/tmp", "all_mountpoints": ["/var/tmp"],
                    "visible_to_plugin": False, "visibility_note": "n/a",
                },
            ],
        },
    }
    assert _run_checks(ctx, env)["storage_visible_to_plugin"].status == "na"


def test_storage_visibility_flags_an_invisible_sd_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The failure the storage block exists for."""
    ctx = _ctx(monkeypatch, tmp_path)
    env = {
        "storage": {
            "devices": [
                {
                    "name": "mmcblk0p1", "class": "sdcard",
                    "mounted_at": "/run/media/deck/SD",
                    "all_mountpoints": ["/run/media/deck/SD"],
                    "visible_to_plugin": False,
                    "visibility_note": "FUSE mount: check allow_other",
                },
            ],
        },
    }
    verdict = _run_checks(ctx, env)["storage_visible_to_plugin"]
    assert verdict.status == "fail"
    assert "mmcblk0p1" in verdict.detail


def test_a_check_that_raises_is_isolated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """One broken check must not cost us the other twenty."""
    ctx = _ctx(monkeypatch, tmp_path)

    def _explode(_view: Any) -> Any:
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(checks, "_check_not_root", _explode)
    monkeypatch.setattr(
        checks, "_CHECKS", (_explode, checks._check_data_dir_writable),
    )
    results = checks.run_checks(ctx, path_audit.audit_paths(ctx), {})
    statuses = {item.status for item in results}
    assert "error" in statuses
    assert len(results) == 2


def test_checks_never_write_to_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A diagnostic must never mutate what it is describing."""
    ctx = _ctx(monkeypatch, tmp_path)
    data = Path(ctx.roots["data"] or "")
    _write(data / "settings.json", "{}")
    _write(data / "games.map", "1 epic a\n")
    before = _snapshot(Path(str(ctx.paths.data_dir)).parent)
    checks.run_checks(ctx, path_audit.audit_paths(ctx), {})
    path_audit.audit_paths(ctx)
    assert _snapshot(Path(str(ctx.paths.data_dir)).parent) == before


def _snapshot(root: Path) -> set[tuple[str, int]]:
    """Every path under ``root`` with its size."""
    found: set[tuple[str, int]] = set()
    for path in root.rglob("*"):
        found.add((str(path), path.stat().st_size if path.is_file() else -1))
    return found


def test_render_includes_verdicts_and_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    ctx = _ctx(monkeypatch, tmp_path)
    records = path_audit.audit_paths(ctx)
    results = checks.run_checks(ctx, records, {})
    text = checks.render_checks(results) + path_audit.render_audit(ctx, records)
    assert "SANITY CHECKS" in text
    assert "PATH AUDIT" in text
    assert "ROOTS" in text
    assert "shortcuts_vdf" in text
    assert "EXPECT" in text
