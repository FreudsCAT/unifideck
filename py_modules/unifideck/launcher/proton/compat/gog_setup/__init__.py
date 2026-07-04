"""compat/gog_setup — GOG first-launch redistributable + script setup.

Faithful port of Heroic's ``setup.ts`` (staging's ``gog_setup.py``),
adapted to the launcher architecture and split for LOC control:

* :mod:`common`  — paths, manifest loaders, wine exec, lang map
* :mod:`redist`  — download (gogdl) + install redistributables
* :mod:`scripts` — scriptinterpreter / temp_executable + goggame-*.script

``apply_gog_setup(plan, language)`` is the single entry, called once per
prefix (marker-guarded) from the GOG handler before launch. Standalone
— imports nothing from ``unifideck.stores`` (the launcher's slim Python
can't load the GOG store's cryptography chain). Best-effort: failures
log and never block the launch, except a hard redistributable-install
failure which is surfaced to the caller.
"""
from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from .common import (
    get_dependencies,
    load_manifest,
    load_redist_manifest,
    wait_for_prefix_ready,
)
from .redist import ensure_redist_downloaded, install_redistributables
from .scripts import (
    apply_script_registry,
    run_script_interpreter,
    run_temp_executable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

_MARKER_NAME = ".unifideck-gog-setup-done"
# Versioned marker for the script-registry step alone. Bumped to ``.v2`` when
# the dual-WOW64-view write landed; existing prefixes (whose only marker is the
# heavy ``_MARKER_NAME``) re-apply their registry script once so 32-bit GOG
# titles stop showing "Install". Bump the suffix again if the write changes.
_REG_MARKER = ".unifideck-gog-script-reg.v2"


def _prefix_root(plan: ProtonLaunchPlan) -> Path:
    p = plan.prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p


async def apply_gog_setup(
    plan: ProtonLaunchPlan, language: str = "en-US",
) -> None:
    """Install GOG redistributables + apply setup-script registry once."""
    prefix_root = _prefix_root(plan)
    game_id = plan.context.game_id
    install_path = str(plan.context.work_dir or plan.context.exe_path.parent)

    if not wait_for_prefix_ready(prefix_root):
        return  # prefix not initialised yet; next launch retries

    # Always ensure the game's setRegistry script is applied (both WOW64 views)
    # — guarded by its own versioned marker, INDEPENDENT of the heavy-setup
    # marker below, so prefixes built before the dual-view fix self-heal.
    await _ensure_script_registry(plan, game_id, install_path, prefix_root)

    marker = prefix_root / _MARKER_NAME
    if marker.is_file():
        logger.debug("[gog_setup] heavy setup already done for %s", prefix_root)
        return

    manifest = load_manifest(game_id)
    if manifest is None:
        _write_marker(marker)
        logger.info("[gog_setup] complete for %s (no manifest)", game_id)
        return

    deps = get_dependencies(manifest)
    logger.info("[gog_setup] %s deps: %s", game_id, ", ".join(deps) or "none")
    if deps:
        await ensure_redist_downloaded(plan, deps)
    await _run_setup_scripts(plan, game_id, manifest, install_path, language)
    if deps:
        await _install_redists(plan, deps)
    _write_marker(marker)
    logger.info("[gog_setup] complete for %s", game_id)


async def _run_setup_scripts(
    plan: ProtonLaunchPlan,
    game_id: str,
    manifest: dict[str, Any],
    install_path: str,
    language: str,
) -> None:
    """Run the v2 scriptInterpreter / temp-executable setup step, if any."""
    if manifest.get("version") != 2:
        return
    if manifest.get("scriptInterpreter"):
        await run_script_interpreter(
            plan, game_id, manifest, install_path, language,
        )
    else:
        await run_temp_executable(
            plan, game_id, manifest, install_path, language,
        )


async def _install_redists(plan: ProtonLaunchPlan, deps: list[str]) -> None:
    """Install downloaded redistributables from the redist manifest."""
    redist_manifest = load_redist_manifest()
    if redist_manifest is None:
        logger.warning("[gog_setup] no redist manifest found")
        return
    await install_redistributables(plan, deps, redist_manifest)


async def _ensure_script_registry(
    plan: ProtonLaunchPlan, game_id: str, install_path: str, prefix_root: Path,
) -> None:
    """Apply ``goggame-*.script`` setRegistry actions once (versioned marker).

    Separate from the heavy-setup marker so existing prefixes re-apply their
    registry script (now to both WOW64 views) exactly once on next launch.
    Idempotent: ``reg.exe add /f`` overwrites, so a re-run is harmless.
    """
    reg_marker = prefix_root / _REG_MARKER
    if reg_marker.is_file():
        return
    await apply_script_registry(plan, game_id, install_path)
    _write_marker(reg_marker)
    logger.info("[gog_setup] script registry applied for %s", game_id)


def _write_marker(marker: Path) -> None:
    with contextlib.suppress(OSError):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done", encoding="utf-8")


__all__ = ["apply_gog_setup"]
