"""Prefix identity invariant: .template is always derived from .upc-auth.

After the fix, :class:`_TemplatePrefixBuilder` ensures:

- ``.template`` is an rsync clone of ``.upc-auth`` when auth exists,
  not a standalone fresh install.
- Shared ``MachineGuid`` + registry DPAPI state across all prefixes.
- ``regenerate_template_from_auth_if_diverged`` realigns the template
  when identities diverge (migration for already-broken installs).

These tests use real :class:`UbisoftConfig` / :class:`UbisoftPrefixPaths`
instances (no mocks) with a fake ``tmp_path`` prefix tree.  The silent
installer and rsync are stubbed at the helpers layer.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from unifideck.stores.ubisoft.config import UbisoftConfig


# ── helpers to write a minimal prefix with a known MachineGuid ──────────
def _write_system_reg(prefix: Path, guid: str) -> None:
    pfx = prefix / "pfx"
    pfx.mkdir(parents=True, exist_ok=True)
    (pfx / "system.reg").write_text(
        '[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Cryptography]\n'
        f'"MachineGuid"="{guid}"\n',
        encoding="utf-8",
    )


_CSS_REL = Path("drive_c") / "users" / "steamuser" / "AppData" / "Local" / "Ubisoft Game Launcher" / "ConnectSecureStorage.dat"


def _write_upc_stub(prefix: Path) -> None:
    """Write a fake upc.exe so find_upc_exe succeeds."""
    upc = (
        prefix
        / "drive_c"
        / "Program Files (x86)"
        / "Ubisoft"
        / "Ubisoft Game Launcher"
        / "upc.exe"
    )
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.touch()


def _write_fake_creds(prefix: Path) -> None:
    """Write a ConnectSecureStorage.dat large enough to be "valid" (>100 B)."""
    css = prefix / _CSS_REL
    css.parent.mkdir(parents=True, exist_ok=True)
    css.write_bytes(b"\x00" * 200)


def _write_bootstrap_marker(prefix: Path, source: str, space_id: str | None = None) -> None:
    marker = prefix / "unifideck_ubisoft_bootstrap.marker"
    lines = [source]
    if space_id:
        lines.insert(1, f"game={space_id}")
    marker.write_text("\n".join(lines) + "\n", encoding="utf-8")


_CSS_MIN = 100  # mirrors reader._CSS_MIN_VALID_SIZE


def _has_creds(prefix: Path) -> bool:
    css = prefix / _CSS_REL
    return css.is_file() and css.stat().st_size > _CSS_MIN


# ── helpers to build real UbisoftConfig / UbisoftPrefixPaths ────────────
def _cfg(prefixes_root: Path) -> UbisoftConfig:
    return UbisoftConfig(prefixes_dir=str(prefixes_root))


def _paths(cfg: UbisoftConfig):
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths
    return UbisoftPrefixPaths(config=cfg)


def _binaries(cfg: UbisoftConfig):
    from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver
    return UbisoftBinaryResolver(config=cfg)


# ── stubbed helpers (no real rsync / no real installer) ─────────────────
class _FakeInstallerCache:
    async def ensure_cached(self) -> str | None:
        return "/fake/UbisoftConnectInstaller.exe"


async def _fake_silent_installer(prefix_dir: str, *, prefix_path_hint: str | None = None, **kw) -> bool:
    """Pretend the installer succeeded: write upc.exe + system.reg (fresh GUID)."""
    import uuid
    prefix = Path(prefix_dir if prefix_path_hint is None else prefix_path_hint) or Path(prefix_dir)
    _write_system_reg(prefix, str(uuid.uuid4()))
    _write_upc_stub(prefix)
    return True


# ── build the real objects, then patch ───────────────────────────────────
def _builds(tmp_path: Path):
    """Return (template_builder, helpers, config).

    Stubs rsync + silent_installer so tests don't touch the filesystem
    outside ``tmp_path``.
    """
    from unifideck.stores.ubisoft.prefix.helpers import _PrefixHelpers
    from unifideck.stores.ubisoft.prefix.template_builder import _TemplatePrefixBuilder

    cfg = _cfg(tmp_path)
    paths_ = _paths(cfg)

    # Fake parent object for _PrefixHelpers — only needs the
    # attributes that the builder methods access.
    class _Parent:
        def __init__(self) -> None:
            self._config = cfg
            self._paths = paths_

        def _inject_auth_state(self, prefix_paths: list[str]) -> int:
            return len(prefix_paths)

        def template_exists(self) -> bool:
            marker = Path(cfg.template_dir_expanded) / cfg.bootstrap_marker
            return marker.is_file()

    parent = _Parent()

    helpers = _PrefixHelpers.__new__(_PrefixHelpers)
    helpers._parent = parent  # type: ignore[attr-defined]

    tb = _TemplatePrefixBuilder(
        config=cfg,
        paths=paths_,
        helpers=helpers,
        installer_cache=_FakeInstallerCache(),  # type: ignore[arg-type]
    )

    # Stub rsync (no real subprocess)
    helpers.rsync_clone = _stub_rsync  # type: ignore[method-assign]

    # Stub helpers.run_silent_installer so it doesn't really exec umu
    async def _stubbed_silent(*, prefix_dir: str, **kw: object) -> bool:
        import uuid
        p = Path(prefix_dir)
        _write_system_reg(p, str(uuid.uuid4()))
        _write_upc_stub(p)
        return True

    helpers.run_silent_installer = _stubbed_silent  # type: ignore[method-assign]

    return tb, helpers, cfg


async def _stub_rsync(src: str, dst: str, *, exclude_games: bool) -> bool:
    """Stub rsync: shallow copy that picks up system.reg + upc.exe.

    Real rsync copies the full drive_c — for tests we only need
    MachineGuid + upc.exe + optional creds.
    """
    import shutil
    src_p = Path(src)
    dst_p = Path(dst)
    dst_p.mkdir(parents=True, exist_ok=True)

    # copy system.reg if present
    for reg in ("pfx/system.reg", "system.reg"):
        s = src_p / reg
        if s.is_file():
            (dst_p / reg).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dst_p / reg)

    # copy upc.exe
    upc_rel = Path("drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe")
    s_upc = src_p / upc_rel
    if s_upc.is_file():
        (dst_p / upc_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s_upc, dst_p / upc_rel)

    # copy creds if present
    css_rel = _CSS_REL
    s_css = src_p / css_rel
    if s_css.is_file():
        (dst_p / css_rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s_css, dst_p / css_rel)

    return True


# ═══════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_template_derived_from_auth_when_auth_exists(tmp_path):
    """ensure_template_prefix derives from auth when .upc-auth exists."""
    tb, helpers, cfg = _builds(tmp_path)

    # Create an auth prefix (simulating a fresh install)
    auth = tmp_path / ".upc-auth"
    _write_system_reg(auth, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _write_upc_stub(auth)
    _write_bootstrap_marker(auth, "auth_prefix")

    # Auth prefix exists, so ensure_template_prefix should derive from it
    await tb.ensure_template_prefix()

    template = tmp_path / ".template"
    assert tb.template_exists(), "template should exist after ensure_template_prefix"
    assert tb.read_machine_guid(template) == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", (
        "template GUID must match auth GUID (derived from auth)"
    )


@pytest.mark.asyncio
async def test_template_fresh_install_when_no_auth(tmp_path):
    """ensure_template_prefix falls back to fresh install when no auth."""
    tb, helpers, cfg = _builds(tmp_path)

    # No auth prefix at all
    await tb.ensure_template_prefix()

    template = tmp_path / ".template"
    assert tb.template_exists()
    assert tb.read_machine_guid(template), "template should have a GUID"


@pytest.mark.asyncio
async def test_regenerate_template_from_auth_if_diverged(tmp_path):
    """Diverged template is realigned to auth, login preserved."""
    tb, helpers, cfg = _builds(tmp_path)

    auth = tmp_path / ".upc-auth"
    template = tmp_path / ".template"

    # Auth prefix with login
    AUTH_GUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _write_system_reg(auth, AUTH_GUID)
    _write_upc_stub(auth)
    _write_fake_creds(auth)
    _write_bootstrap_marker(auth, "auth_prefix")

    # Template with DIFFERENT GUID and NO login (broken state)
    _write_system_reg(template, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    _write_upc_stub(template)
    _write_bootstrap_marker(template, "template")
    # no creds in template

    assert tb.template_exists()
    assert not _has_creds(template), "pre-condition: template lacks creds"
    assert tb.read_machine_guid(auth) != tb.read_machine_guid(template), "pre-condition: GUIDs differ"

    await tb.regenerate_template_from_auth_if_diverged()

    # Template should now share auth GUID AND have credentials
    assert tb.read_machine_guid(template) == AUTH_GUID, "template GUID must realign to auth"
    assert _has_creds(template), "template must have credentials after realignment"
    # Auth prefix must be untouched
    assert _has_creds(auth), "auth login must survive migration"


@pytest.mark.asyncio
async def test_regenerate_noop_when_already_aligned(tmp_path):
    """regenerate_template_from_auth_if_diverged is a no-op when aligned."""
    tb, helpers, cfg = _builds(tmp_path)

    AUTH_GUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    auth = tmp_path / ".upc-auth"
    _write_system_reg(auth, AUTH_GUID)
    _write_upc_stub(auth)
    _write_fake_creds(auth)
    _write_bootstrap_marker(auth, "auth_prefix")

    template = tmp_path / ".template"
    _write_system_reg(template, AUTH_GUID)  # same GUID
    _write_upc_stub(template)
    _write_fake_creds(template)  # already has creds
    _write_bootstrap_marker(template, "template")

    # Record template state before
    tmpl_mtime_before = (template / _CSS_REL).stat().st_mtime if (template / _CSS_REL).is_file() else 0

    await tb.regenerate_template_from_auth_if_diverged()

    # Template should be untouched
    assert tb.read_machine_guid(template) == AUTH_GUID
    assert _has_creds(template)
    tmpl_mtime_after = (template / _CSS_REL).stat().st_mtime
    assert tmpl_mtime_after == tmpl_mtime_before, "template should not be touched when aligned"


@pytest.mark.asyncio
async def test_regenerate_noop_when_no_auth(tmp_path):
    """regenerate_template_from_auth_if_diverged is no-op when auth missing."""
    tb, helpers, cfg = _builds(tmp_path)

    template = tmp_path / ".template"
    _write_system_reg(template, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    _write_upc_stub(template)
    _write_bootstrap_marker(template, "template")

    # No .upc-auth at all
    await tb.regenerate_template_from_auth_if_diverged()

    # Template untouched
    assert tb.template_exists()
    assert tb.read_machine_guid(template) == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_regenerate_noop_when_auth_has_no_creds(tmp_path):
    """regenerate_template_from_auth_if_diverged no-op when auth lacks creds."""
    tb, helpers, cfg = _builds(tmp_path)

    auth = tmp_path / ".upc-auth"
    _write_system_reg(auth, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _write_upc_stub(auth)
    _write_bootstrap_marker(auth, "auth_prefix")
    # NO creds in auth

    template = tmp_path / ".template"
    _write_system_reg(template, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    _write_upc_stub(template)
    _write_bootstrap_marker(template, "template")

    await tb.regenerate_template_from_auth_if_diverged()

    # Template untouched (auth has no creds, so nothing to propagate)
    assert tb.read_machine_guid(template) == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert not _has_creds(template)


@pytest.mark.asyncio
async def test_fresh_auth_then_template_then_game_share_guid(tmp_path):
    """End-to-end: fresh auth → template → game all share one GUID."""
    tb, helpers, cfg = _builds(tmp_path)

    # 1. Create auth prefix (fresh install)
    auth = tmp_path / ".upc-auth"
    _write_system_reg(auth, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    _write_upc_stub(auth)
    _write_fake_creds(auth)
    _write_bootstrap_marker(auth, "auth_prefix")

    # 2. Derive template from auth
    await tb.ensure_template_prefix()
    template = tmp_path / ".template"

    # 3. Clone game prefix from template (simulating clone_prefix_from_template)
    game = tmp_path / "test_game"
    import shutil
    # shallow clone: just copy system.reg + upc.exe
    _write_system_reg(game, tb.read_machine_guid(template))
    _write_upc_stub(game)

    AUTH_GUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert tb.read_machine_guid(auth) == AUTH_GUID
    assert tb.read_machine_guid(template) == AUTH_GUID
    assert tb.read_machine_guid(game) == AUTH_GUID
