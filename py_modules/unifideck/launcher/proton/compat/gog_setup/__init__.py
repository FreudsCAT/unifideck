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
from typing import TYPE_CHECKING

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
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

_MARKER_NAME = ".unifideck-gog-setup-done"


def _prefix_root(plan: ProtonLaunchPlan):
    p = plan.prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p


async def apply_gog_setup(
    plan: ProtonLaunchPlan, language: str = "en-US",
) -> None:
    """Install GOG redistributables + apply setup-script registry once."""
    prefix_root = _prefix_root(plan)
    marker = prefix_root / _MARKER_NAME
    if marker.is_file():
        logger.debug("[gog_setup] already done for %s", prefix_root)
        return

    game_id = plan.context.game_id
    install_path = str(plan.context.work_dir or plan.context.exe_path.parent)

    if not wait_for_prefix_ready(prefix_root):
        return  # prefix not initialised yet; next launch retries

    manifest = load_manifest(game_id)
    if manifest is None:
        # Nothing to set up, but apply any registry script the game ships.
        await apply_script_registry(plan, game_id, install_path)
        _write_marker(marker)
        return

    deps = get_dependencies(manifest)
    logger.info("[gog_setup] %s deps: %s", game_id, ", ".join(deps) or "none")

    if deps:
        await ensure_redist_downloaded(plan, deps)

    if manifest.get("version") == 2:
        if manifest.get("scriptInterpreter"):
            await run_script_interpreter(
                plan, game_id, manifest, install_path, language,
            )
        else:
            await run_temp_executable(
                plan, game_id, manifest, install_path, language,
            )

    if deps:
        redist_manifest = load_redist_manifest()
        if redist_manifest is not None:
            await install_redistributables(plan, deps, redist_manifest)
        else:
            logger.warning("[gog_setup] no redist manifest found")

    await apply_script_registry(plan, game_id, install_path)
    _write_marker(marker)
    logger.info("[gog_setup] complete for %s", game_id)


def _write_marker(marker) -> None:
    with contextlib.suppress(OSError):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("done", encoding="utf-8")


__all__ = ["apply_gog_setup"]
