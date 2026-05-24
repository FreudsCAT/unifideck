"""services/bootstrap/startup.py — Async start hooks + post-boot self-heal.

Calls ``start()`` on services that need async initialisation,
each wrapped in its own try/except so one broken service can't
block the others. Then runs a post-boot self-heal that restores
the +x bit on launcher entry points.
"""
from __future__ import annotations

import logging
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import ServiceContainer

logger = logging.getLogger(__name__)

# Services with async init hooks. First three open DBs or spawn
# poll loops; ``security`` runs device-fingerprint verification;
# ``launch_history`` doesn't truly need async but is listed here
# for uniformity. Other services don't implement ``start`` and
# are skipped by the getattr probe below.
_ASYNC_START_SERVICES: tuple[str, ...] = (
    "download",
    "account",
    "playtime",
    "security",
    "launch_history",
)


async def start_async_services(container: ServiceContainer) -> None:
    """Await ``start`` on each entry in ``_ASYNC_START_SERVICES``.

    Missing service (None slot) → skip. Missing ``start`` method
    → skip. Failed start → log WARNING + continue (broken DB open
    or fingerprint check leaves that service disabled but plugin
    still boots). Always runs the executable-bit self-heal at
    the end.
    """
    for service_name in _ASYNC_START_SERVICES:
        instance = getattr(container, service_name, None)
        if instance is None:
            continue

        start_method = getattr(instance, "start", None)
        if not callable(start_method):
            continue

        try:
            await start_method()
            logger.info("[Startup] started %s", service_name)
        except Exception as e:
            logger.warning(
                "[Startup] failed to start %s: %s",
                service_name, e,
            )

    if container.shortcut is not None:
        try:
            await _self_heal_auth_shortcuts(container.shortcut)
        except Exception as e:
            logger.warning("[Startup] failed to run self-heal auth shortcuts: %s", e)

    _self_heal_executable_bits()


def _self_heal_executable_bits() -> None:
    """Restore +x on launcher entry points after Decky Loader unzip.

    Decky Loader's unzip doesn't always preserve the
    ``external_attr`` field, so ``dispatcher.py`` can land
    without +x → execve fails with "Permission denied" even
    though the shebang is correct. Runs BEFORE the shortcut
    migration so when shortcuts are rewritten to point at the
    dispatcher it's already executable. Best-effort — failure
    logged but plugin continues to boot (recoverable via manual
    chmod +x).
    """
    try:
        # Get path to the bin directory relative to this file
        # This file is at py_modules/unifideck/services/bootstrap/startup.py
        base_dir = str(Path(str(Path(str(Path(str(Path(str(Path(__file__).parent)).parent)).parent)).parent)).parent)
        bin_dir = str(Path(base_dir) / "bin")

        if not Path(bin_dir).is_dir():
            return

        for filename in [entry.name for entry in Path(bin_dir).iterdir()]:
            path = str(Path(bin_dir) / filename)
            if Path(path).is_file():
                st = Path(path).stat()
                # Add executable bit for owner/group/others if not present
                if not (st.st_mode & stat.S_IXUSR):
                    # Adds the +x bit for owner/group/others on shipped
                    # tools. The mask preserves all existing bits and
                    # only adds executability — required for the
                    # bundled helpers to run after unzip strips +x.
                    Path(path).chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    logger.info("[Startup] restored +x on %s", path)
    except Exception as e:
        logger.warning("[Startup] failed to self-heal executable bits: %s", e)


async def _self_heal_auth_shortcuts(shortcut_svc: Any) -> None:
    """Scan and upgrade any legacy auth shortcuts in shortcuts.vdf to point at the wrapper.

    Ensures that GOG, Epic, Amazon, and Microsoft auth shortcuts are pointing to the
    canonical unifideck-launcher wrapper rather than dispatcher.py, and updates
    their AppID values to reflect the new path so they match Steam's internal CRC32 algorithm.
    """
    try:
        await shortcut_svc._load_shortcuts()
        if not isinstance(shortcut_svc._shortcuts, dict) or "shortcuts" not in shortcut_svc._shortcuts:
            return

        shortcuts_dict = shortcut_svc._shortcuts["shortcuts"]
        if not isinstance(shortcuts_dict, dict):
            return

        changed = False
        launcher_path = shortcut_svc._launcher_path
        if not launcher_path:
            logger.warning("[Startup] shortcut self-healing skipped: _launcher_path is empty")
            return

        # Canonical configs for each store's auth shortcut
        auth_configs = {
            "gog":       {"title": "GOG Sign-In",          "tag": "auth-gog"},
            "epic":      {"title": "Epic Games Sign-In",   "tag": "auth-epic"},
            "amazon":    {"title": "Amazon Games Sign-In", "tag": "auth-amazon"},
            "microsoft": {"title": "Microsoft Sign-In",    "tag": "auth-microsoft"},
        }

        for key, entry in list(shortcuts_dict.items()):
            if not isinstance(entry, dict):
                continue
            tags = entry.get("tags", {})
            if not isinstance(tags, dict):
                continue
            tag_values = list(tags.values())
            has_unifideck_tag = any(t == "Unifideck" for t in tag_values)
            if not has_unifideck_tag:
                continue

            for store, cfg in auth_configs.items():
                if any(t == cfg["tag"] for t in tag_values):
                    # Check if Exe, LaunchOptions, or AppID need correction
                    current_exe = entry.get("Exe") or entry.get("exe")
                    current_opts = entry.get("LaunchOptions")
                    expected_opts = f"{store}:{'ms' if store == 'microsoft' else store}-auth UNIFIDECK_{store.upper()}_ACTION=auth"
                    
                    # We compute the canonical appid based on launcher_path and title
                    expected_appid = shortcut_svc.generate_app_id(launcher_path, cfg["title"])

                    # Determine if we need to update
                    needs_update = (
                        current_exe != launcher_path
                        or current_opts != expected_opts
                        or entry.get("appid") != expected_appid
                        or "Exe" not in entry  # Ensure uppercase key 'Exe' is present
                    )

                    if needs_update:
                        logger.info(
                            "[Startup] Repairing stale/legacy auth shortcut for %s (key=%s): "
                            "exe=%s -> %s, appid=%s -> %s",
                            store, key, current_exe, launcher_path, entry.get("appid"), expected_appid
                        )
                        # Set canonical values
                        entry["Exe"] = launcher_path
                        if "exe" in entry:
                            del entry["exe"]  # Standardise on uppercase Exe
                        entry["LaunchOptions"] = expected_opts
                        entry["appid"] = expected_appid
                        changed = True

        if changed:
            await shortcut_svc._save_all()
            logger.info("[Startup] Stale/legacy auth shortcuts successfully repaired and saved to shortcuts.vdf")
    except Exception as e:
        logger.exception("[Startup] Failed to reconcile/repair stale auth shortcuts: %s", e)

