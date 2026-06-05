"""compat/gog.py — GOG-specific launch helpers (Comet + launcher fallback).

* :func:`start_comet` — launch the bundled ``comet`` (a GOG Galaxy SDK
  reimplementation) in the background so the game gets online features
  (achievements, multiplayer). Tokens come from the GOG token file.
* :func:`resolve_fallback_exe` — when a GOG game exits suspiciously fast,
  detect that the primary ``goggame-*.info`` playTask is a launcher/tool
  stub and return the real game-category exe to retry with.

Standalone: no ``unifideck.stores`` imports (launcher's slim Python).
"""
from __future__ import annotations

import contextlib
import glob
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.launcher.proton.infrastructure.umu_runtime import (
    run_umu_with_retry,
)

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

# gogdl's auth file is plain JSON, keyed by GOG OAuth client_id →
# {access_token, refresh_token, ...}. It uses the GOG Galaxy client —
# the same tokens Comet needs — and unlike the plugin's encrypted
# ``gog_token.json`` it's readable from the slim launcher process
# (which can't load the cryptography chain to decrypt the other one).
_GOGDL_AUTH_FILE = Path("~/.config/unifideck/gogdl/auth.json").expanduser()
# A launched exe that exits faster than this is treated as a possible
# broken launcher stub (real launchers/games run far longer).
EARLY_EXIT_SECONDS = 15


def _read_gog_tokens() -> tuple[str, str, str] | None:
    """Return (access, refresh, user_id) from gogdl's plain auth, or None."""
    if not _GOGDL_AUTH_FILE.is_file():
        return None
    try:
        data = json.loads(_GOGDL_AUTH_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        access = entry.get("access_token") or ""
        refresh = entry.get("refresh_token") or ""
        if access and refresh:
            return access, refresh, str(entry.get("user_id") or "")
    return None


def start_comet(plan: ProtonLaunchPlan) -> subprocess.Popen[bytes] | None:
    """Start Comet (GOG Galaxy SDK) in the background, or None.

    Best-effort: missing binary/tokens just means no online features.
    """
    comet = plan.context.plugin_dir / "bin" / "comet"
    if not comet.is_file():
        return None
    tokens = _read_gog_tokens()
    if tokens is None:
        logger.info("[compat.gog] no GOG tokens — Comet online features off")
        return None
    access, refresh, user_id = tokens
    args = [
        str(comet),
        "--username", "GOGUser",
        "--access-token", access,
        "--refresh-token", refresh,
        "--quit",
    ]
    if user_id:
        args += ["--user-id", user_id]
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("[compat.gog] Comet started (pid=%s)", proc.pid)
        return proc
    except OSError as e:
        logger.warning("[compat.gog] Comet failed to start: %s", e)
        return None


def _load_play_tasks(info_file: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(info_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tasks = data.get("playTasks", []) if isinstance(data, dict) else []
    return [t for t in tasks if isinstance(t, dict)]


def resolve_fallback_exe(install_path: str) -> str | None:
    """Return the real game exe when the primary playTask is a stub.

    Only fires when the primary ``goggame-*.info`` playTask is a
    ``launcher``/``tool`` category — real game-category primaries are
    never bypassed (mirrors staging). Returns the first game-category
    ``FileTask`` exe that exists on disk, or None.
    """
    for info_file in glob.glob(os.path.join(install_path, "goggame-*.info")):
        tasks = _load_play_tasks(info_file)
        primary = next((t for t in tasks if t.get("isPrimary")), None)
        if not primary or str(
            primary.get("category", "")
        ).lower() not in ("launcher", "tool"):
            return None
        for t in tasks:
            if (
                t.get("category") == "game"
                and t.get("type") == "FileTask"
                and t.get("path")
            ):
                candidate = os.path.join(
                    install_path, str(t["path"]).replace("\\", "/"),
                )
                if os.path.isfile(candidate):
                    return candidate
        return None
    return None


def _install_language(work_dir: Path) -> str:
    """Read the install-time language from the ``.unifideck-id`` marker."""
    marker = work_dir / ".unifideck-id"
    if marker.is_file():
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(marker.read_text(encoding="utf-8"))
            return str(data.get("language") or "en-US")
    return "en-US"


async def _run_umu_exe(plan: ProtonLaunchPlan, exe_path: Path) -> int:
    """Run a Windows exe through umu (shared by primary + fallback)."""
    cwd = exe_path.parent if exe_path.parent.is_dir() else None
    argv: list[str] = list(plan.state.wrappers)
    argv.extend([str(plan.python_bin), str(plan.umu_wrapper), str(exe_path)])
    argv.extend(plan.state.game_args)
    return await run_umu_with_retry(
        argv, env=plan.env, cwd=cwd, on_start=plan.on_process_start,
    )


async def run_gog_launch(plan: ProtonLaunchPlan) -> int:
    """Full GOG Windows launch: setup → Comet → run (with stub fallback).

    Returns the umu exit code; ``generic_launch`` maps it to a Result.
    GOG *native* (start.sh) never reaches here — it goes via launch_native.
    """
    work_dir = Path(plan.context.work_dir or plan.context.exe_path.parent)

    # Per-game language (goggame-*.info) — best-effort.
    try:
        from unifideck.config.config_manager import ConfigManager
        from unifideck.launcher.proton.language_setup import apply_gog_language
        cfg = ConfigManager(
            str(plan.context.plugin_dir / "defaults" / "config.json"),
        )
        apply_gog_language(plan.context.game_id, str(work_dir), config=cfg)
    except Exception:
        logger.warning("[compat.gog] language setup failed", exc_info=True)

    # GalaxyCommunication.exe stub (offline SDK).
    try:
        from unifideck.launcher.proton.fixes.galaxy_stub import (
            install_galaxy_stub,
        )
        install_galaxy_stub(
            str(plan.prefix_path), plugin_dir=plan.context.plugin_dir,
        )
    except Exception:
        logger.warning("[compat.gog] galaxy stub failed", exc_info=True)

    # GOG redistributables + setup scripts (first launch, marker-guarded).
    try:
        from .gog_setup import apply_gog_setup
        await apply_gog_setup(plan, _install_language(work_dir))
    except Exception:
        logger.exception("[compat.gog] gog_setup failed (non-fatal)")

    plan.env["PROTON_ENABLE_NVAPI"] = "1"

    comet = start_comet(plan)
    try:
        start = time.monotonic()
        rc = await _run_umu_exe(plan, plan.context.exe_path)
        elapsed = time.monotonic() - start
        # Broken launcher stub? Retry with the real game exe.
        if rc != 0 and elapsed < EARLY_EXIT_SECONDS:
            fallback = resolve_fallback_exe(str(work_dir))
            if fallback and fallback != str(plan.context.exe_path):
                logger.info(
                    "[compat.gog] launcher stub exited in %ds (rc=%d), "
                    "retrying game exe: %s", int(elapsed), rc, fallback,
                )
                rc = await _run_umu_exe(plan, Path(fallback))
        return rc
    finally:
        if comet is not None:
            with contextlib.suppress(Exception):
                comet.terminate()
