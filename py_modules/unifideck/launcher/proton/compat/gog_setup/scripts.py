"""compat/gog_setup/scripts.py — GOG setup-script execution + registry.

Ports Heroic ``setup.ts`` script handling: run the v2 setup executable
(``scriptinterpreter.exe`` / per-product ``temp_executable``) and apply
``goggame-*.script`` ``setRegistry`` actions (critical for older Ubisoft
GOG titles). ``Execute`` actions are intentionally skipped — running
arbitrary installers headless in Gaming Mode risks hanging on a GUI
dialog and poisoning the prefix.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .common import REDIST_DIR, SUPPORT_DIR, language_name, run_wine

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

_ROOT_MAP = {
    "HKEY_LOCAL_MACHINE": "HKLM", "HKLM": "HKLM",
    "HKEY_CURRENT_USER": "HKCU", "HKCU": "HKCU",
    "HKEY_CLASSES_ROOT": "HKCR", "HKCR": "HKCR",
}
_TYPE_MAP = {
    "string": "REG_SZ", "dword": "REG_DWORD", "binary": "REG_BINARY",
    "expandstring": "REG_EXPAND_SZ", "multistring": "REG_MULTI_SZ",
}


def _setup_args(
    manifest: dict[str, Any], product_id: str, install_path: str, lang: str,
) -> list[str]:
    """Build the GOG silent-setup arg list (Heroic setup.ts)."""
    name = language_name(lang)
    return [
        "/VERYSILENT", f"/DIR={install_path}",
        f"/Language={name}", f"/LANG={name}",
        f"/ProductId={product_id}", "/galaxyclient",
        f"/buildId={manifest.get('buildId', '0')}",
        f"/versionName={manifest.get('version_name', '1.0')}",
        f"/lang-code={lang}", f"/supportDir={SUPPORT_DIR / product_id}",
        "/nodesktopshorctut", "/nodesktopshortcut",  # GOG's own typo + correct
    ]


async def run_script_interpreter(
    plan: ProtonLaunchPlan, game_id: str,
    manifest: dict[str, Any], install_path: str, lang: str,
) -> None:
    """Run ``scriptinterpreter.exe`` (ISI) for v2 manifests."""
    isi = REDIST_DIR / "__redist" / "ISI" / "scriptinterpreter.exe"
    if not isi.is_file():
        logger.warning("[gog_setup] scriptinterpreter.exe missing")
        return
    for product in manifest.get("products", []) or []:
        pid = product.get("productId") if isinstance(product, dict) else None
        if not pid:
            continue
        await run_wine(
            plan, str(isi), _setup_args(manifest, pid, install_path, lang),
        )


async def run_temp_executable(
    plan: ProtonLaunchPlan, game_id: str,
    manifest: dict[str, Any], install_path: str, lang: str,
) -> None:
    """Run a per-product ``temp_executable`` setup (e.g. The Witcher)."""
    for product in manifest.get("products", []) or []:
        if not isinstance(product, dict):
            continue
        temp_exe = product.get("temp_executable") or ""
        if not temp_exe:
            continue
        pid = product.get("productId", game_id)
        exe = SUPPORT_DIR / game_id / pid / temp_exe
        if not exe.is_file():
            logger.warning("[gog_setup] temp_executable missing: %s", exe)
            continue
        await run_wine(
            plan, str(exe), _setup_args(manifest, pid, install_path, lang),
        )


def _win_path(install_path: str) -> str:
    """Map a Linux install path to its Wine ``Z:`` path for ``{app}``."""
    return "Z:" + install_path.replace("/", "\\")


def _wow64_subkeys(root: str, subkey: str) -> list[str]:
    """Return the subkey(s) to write so 32-bit *and* 64-bit games both see it.

    A win64 prefix redirects 32-bit reads of ``HKLM\\Software\\…`` to
    ``HKLM\\Software\\Wow6432Node\\…``. GOG ``setRegistry`` "Installed Path"
    keys (older Bethesda / Ubisoft-on-GOG titles) are 32-bit, so a key written
    only to the native view is invisible to the game → it shows "Install".
    Mirror the Epic/Ubisoft fixes (``epic_registry.py``) and write the literal
    ``Wow6432Node`` path too (don't rely on ``reg.exe /reg:32`` — Wine support
    is version-dependent). Only HKLM\\Software keys are redirected; everything
    else is written once, unchanged.
    """
    subkeys = [subkey]
    prefix = "Software\\"
    if (
        root == "HKLM"
        and subkey.lower().startswith(prefix.lower())
        and "wow6432node" not in subkey.lower()
    ):
        remainder = subkey[len(prefix):]
        subkeys.append(f"Software\\WOW6432Node\\{remainder}")
    return subkeys


async def _apply_set_registry(
    plan: ProtonLaunchPlan, args: dict[str, Any], install_path: str,
) -> None:
    """Apply one ``setRegistry`` action via ``reg.exe add`` (both WOW64 views)."""
    root = _ROOT_MAP.get(args.get("root", ""), args.get("root", ""))
    subkey = args.get("subkey", "")
    if not root or not subkey:
        return
    value_data = args.get("valueData", "")
    if isinstance(value_data, str):
        value_data = value_data.replace("{app}", _win_path(install_path))
    value_name = args.get("valueName", "")
    value_args: list[str] = []
    if value_name:
        reg_type = _TYPE_MAP.get(str(args.get("valueType", "string")).lower(), "REG_SZ")
        value_args = ["/v", value_name, "/t", reg_type, "/d", str(value_data)]
    for target in _wow64_subkeys(root, subkey):
        await run_wine(plan, "reg.exe", ["add", f"{root}\\{target}", "/f", *value_args])


def _load_script_actions(
    install_path: str, game_id: str,
) -> list[tuple[str, list[Any]]]:
    """Find and parse ``goggame-*.script`` files (blocking I/O).

    Returns ``[(script_name, actions), ...]``. Synchronous so the
    async caller can do all the filesystem work in a single
    ``asyncio.to_thread`` hop rather than on the event loop.
    """
    base = Path(install_path)
    scripts = list(base.glob(f"goggame-{game_id}.script")) or list(
        base.glob("goggame-*.script"),
    )
    parsed: list[tuple[str, list[Any]]] = []
    for script_file in scripts:
        try:
            data = json.loads(script_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(
                "[gog_setup] script parse failed %s: %s", script_file, e,
            )
            continue
        actions = data.get("actions", []) if isinstance(data, dict) else []
        parsed.append((script_file.name, actions))
    return parsed


async def apply_script_registry(
    plan: ProtonLaunchPlan, game_id: str, install_path: str,
) -> None:
    """Apply ``goggame-*.script`` setRegistry actions to the prefix."""
    parsed = await asyncio.to_thread(
        _load_script_actions, install_path, game_id,
    )
    for script_name, actions in parsed:
        logger.info(
            "[gog_setup] %s: %d script action(s)", script_name, len(actions),
        )
        for action in actions:
            install = action.get("install", {}) if isinstance(action, dict) else {}
            if install.get("action") == "setRegistry":
                await _apply_set_registry(
                    plan, install.get("arguments", {}) or {}, install_path,
                )
            # 'Execute' actions intentionally skipped (headless hang risk).
