"""launcher/proton/infrastructure/core.py — Shared Proton/UMU launch setup."""
from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from unifideck.launcher.types.context import LaunchContext, RuntimeState
from unifideck.launcher.types.errors import DependencyMissingError

logger = logging.getLogger(__name__)

STORE_TO_UMU = {
    "epic": "egs",
    "gog": "gog",
    "amazon": "amazon",
    "ubisoft": "ubisoft",
    "microsoft": "microsoft",
}


def sanitize_frozen_loader_env(env: dict[str, str]) -> None:
    """Undo a PyInstaller-frozen parent's dynamic-loader pollution, in place.

    When the launch plan is built inside the Decky plugin process — whose
    ``PluginLoader`` is a PyInstaller-frozen binary — ``os.environ`` carries
    ``LD_LIBRARY_PATH=/tmp/_MEIxxxx`` pointing at the loader's *bundled* libs
    (an old ``libcrypto`` lacking ``OPENSSL_3.3.0``). umu-run runs under the
    SYSTEM python, so that path makes its ``import ssl`` fail with
    ``ImportError: libcrypto.so.3: version 'OPENSSL_3.3.0' not found`` — umu
    then aborts, so ``createprefix`` / winetricks silently do nothing (the
    install-time prefix warmup produced empty prefixes for exactly this
    reason). PyInstaller stashes the real pre-launch value in
    ``LD_LIBRARY_PATH_ORIG``; restore it, else drop a ``_MEI`` bundle path.

    A NO-OP outside a frozen parent — e.g. the out-of-process launcher Steam
    spawns has a clean env (no ``_ORIG``, no ``_MEI`` path) — so it's safe to
    run on every launch path, not just the warmup.

    ``LD_PRELOAD`` is handled differently: it is write-once-never. All
    umu-run launches go through pressure-vessel (a container) which has its
    own Steam overlay mechanism — re-exporting the host's
    ``gameoverlayrenderer.so`` via ``LD_PRELOAD`` causes "cannot be
    preloaded" errors and can crash/early-exit the game process
    (``WARNING: Keyboard Interrupt``). The retired bash launcher unset
    ``LD_PRELOAD`` once at startup and never restored it for any Proton/umu
    launch; mirror that here — discard any ``LD_PRELOAD_ORIG`` instead of
    restoring from it, and still drop a ``_MEI``-tainted ``LD_PRELOAD``.
    """
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    elif "/_MEI" in env.get("LD_LIBRARY_PATH", ""):
        env.pop("LD_LIBRARY_PATH", None)

    env.pop("LD_PRELOAD_ORIG", None)
    if "/_MEI" in env.get("LD_PRELOAD", ""):
        env.pop("LD_PRELOAD", None)


@dataclass(frozen=True)
class ProtonLaunchPlan:
    """Everything store handlers need to spawn umu-run."""
    context: LaunchContext
    state: RuntimeState
    python_bin: Path
    umu_wrapper: Path
    prefix_path: Path
    env: dict[str, str]
    on_process_start: Callable[[object], None] | None = None
def _ubisoft_prefix_path(ctx: LaunchContext, prefixes_dir: Path) -> Path:
    """Ubisoft prefix path.

    Games can be installed to a user-picked location (SD / custom); the
    backend records the absolute per-game prefix path in
    ``ubisoft_id_map.json`` (the same file ``_uplay_id_from_id_map`` reads).
    Prefer that; fall back to the fixed internal location for games installed
    before this existed and for the auth shortcut (whose game_id has no
    recorded prefix — it uses ``UNIFIDECK_UBISOFT_PREFIX_NAME=.upc-auth``).
    """
    import json
    import os
    id_map_file = Path("~/.local/share/unifideck/ubisoft_id_map.json").expanduser()
    try:
        data = json.loads(id_map_file.read_text(encoding="utf-8"))
        entry = data.get(ctx.game_id) if isinstance(data, dict) else None
        recorded = entry.get("prefix_path") if isinstance(entry, dict) else None
        if recorded:
            return Path(recorded)
    except (OSError, ValueError):
        pass
    ubi_name = os.environ.get("UNIFIDECK_UBISOFT_PREFIX_NAME") or ctx.game_id
    return prefixes_dir / "ubisoft" / ubi_name
def _resolve_prefix(ctx: LaunchContext) -> Path:
    """Resolve prefix."""
    prefixes_dir = Path("~/.local/share/unifideck/prefixes").expanduser()
    if ctx.store == "ubisoft":
        path = _ubisoft_prefix_path(ctx, prefixes_dir)
    else:
        path = prefixes_dir / ctx.game_id
        while path.name == "pfx":
            path = path.parent
    path.mkdir(parents=True, exist_ok=True)
    return path
def _lookup_umu_id(
 ctx: LaunchContext,
 umu_store: str,
 plugin_dir: Path,
) -> str | None:
    """Lookup UMU ID."""
    helper = plugin_dir / "bin" / "umu_lookup.py"
    if not helper.is_file():
        return None
    try:
        out = subprocess.check_output(
            ["python3", str(helper), ctx.game_id, umu_store],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        text = out.decode().strip()
        return text or None
    except (subprocess.SubprocessError, OSError):
        return None

def _locate_umu_wrapper(proton_path: Path, plugin_dir: Path) -> Path:

    """Locate UMU wrapper.

    Priority matches staging's launcher: the plugin-bundled zipapp at
    ``<plugin>/bin/umu/umu/umu-run`` (the canonical location on Deck
    installs), then any copy beside Proton, then a system ``umu-run``.
    """
    plugin_bundled = plugin_dir / "bin" / "umu" / "umu" / "umu-run"
    if plugin_bundled.is_file():
        return plugin_bundled
    bundled = proton_path.parent / "umu-run"
    if bundled.is_file():
        return bundled
    system = shutil.which("umu-run")
    if system:
        return Path(system)
    raise DependencyMissingError(
        "umu-run not found (not bundled at "
        "<plugin>/bin/umu/umu/umu-run, beside proton, nor in PATH)",
        context={
            "proton_path": str(proton_path),
            "plugin_dir": str(plugin_dir),
        },
    )
def proton_prepare(
 ctx: LaunchContext,
 state: RuntimeState,
 *,
 python_bin: Path,
 proton_path: Path,
 proton_tool_id: str,
 on_process_start: Callable[[object], None] | None = None,
) -> ProtonLaunchPlan:
    """Proton prepare."""
    import os
    umu_store = STORE_TO_UMU.get(ctx.store, "none")
    prefix_path = _resolve_prefix(ctx)
    umu_id = _lookup_umu_id(ctx, umu_store, ctx.plugin_dir)
    umu_wrapper = _locate_umu_wrapper(proton_path, ctx.plugin_dir)
    state.python_bin = python_bin
    state.proton_path = proton_path
    state.proton_tool_id = proton_tool_id
    state.prefix_path = prefix_path
    state.umu_store_code = umu_store
    state.umu_id = umu_id
    state.umu_wrapper = umu_wrapper
    env = dict(os.environ)
    had_ld_preload_orig = "LD_PRELOAD_ORIG" in env
    # Strip the Decky PluginLoader's PyInstaller LD_LIBRARY_PATH pollution so
    # umu-run (system python) doesn't load a stale libcrypto and abort — the
    # cause of empty install-time prefixes. No-op for the clean launcher env.
    sanitize_frozen_loader_env(env)
    env["GAMEID"] = umu_id or "umu-0"
    # Epic is the one store whose ProtonFixes "STORE=egs" defaults are
    # actively harmful rather than just redundant: they winetrick
    # vcrun2022 again (core-dumps inside pressure-vessel) and — the one
    # that actually breaks launches — add a `HKCR\com.epicgames.launcher`
    # registry key that makes the EOS SDK switch to launcher-IPC auth
    # mode, causing an instant exit/hang for any non-Ubisoft Epic title
    # that uses EOS (the retired bash launcher forced STORE=none for
    # exactly this reason). Every umu-run invocation for an Epic launch —
    # createprefix, winetricks, the vcruntime regedit fix, and the game
    # itself — derives its env from this dict, so overriding it here
    # once is what actually keeps ProtonFixes from ever seeing "egs".
    # ``state.umu_store_code`` below keeps the real value for diagnostics.
    env["STORE"] = "none" if ctx.store == "epic" else umu_store
    # PROTONPATH tells umu-run which Proton to use — the *directory*
    # holding the ``proton`` script (``proton_path`` is that script, so
    # use its parent). Without this umu falls back to downloading its
    # own UMU-Proton (or fails), ignoring the tool we selected. Mirrors
    # staging's ``export PROTONPATH``.
    env["PROTONPATH"] = str(proton_path.parent)
    env["STEAM_COMPAT_DATA_PATH"] = str(prefix_path)
    # Pin the game to its per-game prefix. umu-run does NOT derive the
    # prefix from STEAM_COMPAT_DATA_PATH — with no WINEPREFIX it defaults
    # to ``~/Games/umu/$GAMEID`` (e.g. the shared ``umu-0`` when a game
    # has no per-game umu_id). That shared prefix lacks the deps our
    # compat steps install into prefix_path (they set WINEPREFIX
    # explicitly) AND it's not where cloud-save sync writes — so the game
    # would launch in the wrong prefix and never see its saves/deps.
    # Mirrors the compat steps (e.g. compat/winetricks.py).
    env["WINEPREFIX"] = str(prefix_path)
    # Game install dir — some Proton features/protonfixes key off this.
    env["STEAM_COMPAT_INSTALL_PATH"] = str(ctx.work_dir)
    # Let DXVK-NVAPI work on non-NVIDIA / mixed driver setups (harmless
    # on the Deck's AMD GPU; required by some titles' NVAPI probes).
    env["DXVK_NVAPI_ALLOW_OTHER_DRIVERS"] = "1"
    # Do NOT pin STEAM_COMPAT_CLIENT_INSTALL_PATH. umu-run derives it
    # itself; forcing it to ``~/.steam/root`` — a symlink chain on
    # atomic/ostree hosts (Bazzite: ``~`` → /var/home, ``.steam/root`` →
    # steam) — makes pressure-vessel's ``/run/host`` + ``from-host``
    # capsule-capture loop when bwrap resolves ``pv-adverb`` → "Too many
    # levels of symbolic links" (ELOOP), so the game exits code 1 before it
    # starts (Cyberpunk/GOG on Bazzite). The pre-refactor bash launcher
    # deliberately UNSET this and worked on Deck + Bazzite + CachyOS; mirror
    # that — also drop any value Steam passed down, since that's the looping
    # one on atomic hosts.
    env.pop("STEAM_COMPAT_CLIENT_INSTALL_PATH", None)
    env["PROTON_VERB"] = "waitforexitandrun"
    env.update(ctx.env_overrides)
    logger.info(
    "[launcher.proton.core] plan ready: store=%s umu_store=%s "
    "umu_id=%s prefix=%s proton=%s ld_preload=%r had_ld_preload_orig=%s",
    ctx.store, umu_store, umu_id, prefix_path, proton_tool_id,
    env.get("LD_PRELOAD"), had_ld_preload_orig,
   )
    return ProtonLaunchPlan(
        context=ctx,
        state=state,
        python_bin=python_bin,
        umu_wrapper=umu_wrapper,
        prefix_path=prefix_path,
        env=env,
        on_process_start=on_process_start,
    )
