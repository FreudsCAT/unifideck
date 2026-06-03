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
    """Ubisoft prefix path."""
    import os
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
    env["GAMEID"] = umu_id or "umu-0"
    env["STORE"] = umu_store
    # PROTONPATH tells umu-run which Proton to use — the *directory*
    # holding the ``proton`` script (``proton_path`` is that script, so
    # use its parent). Without this umu falls back to downloading its
    # own UMU-Proton (or fails), ignoring the tool we selected. Mirrors
    # staging's ``export PROTONPATH``.
    env["PROTONPATH"] = str(proton_path.parent)
    env["STEAM_COMPAT_DATA_PATH"] = str(prefix_path)
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(
    Path("~/.steam/root").expanduser(),
   )
    env["PROTON_VERB"] = "waitforexitandrun"
    env.update(ctx.env_overrides)
    logger.info(
    "[launcher.proton.core] plan ready: store=%s umu_store=%s "
    "umu_id=%s prefix=%s proton=%s",
    ctx.store, umu_store, umu_id, prefix_path, proton_tool_id,
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
