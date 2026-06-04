"""compat/gog_setup/scripts.py — GOG setup-script execution + registry.

Ports Heroic ``setup.ts`` script handling: run the v2 setup executable
(``scriptinterpreter.exe`` / per-product ``temp_executable``) and apply
``goggame-*.script`` ``setRegistry`` actions (critical for older Ubisoft
GOG titles). ``Execute`` actions are intentionally skipped — running
arbitrary installers headless in Gaming Mode risks hanging on a GUI
dialog and poisoning the prefix.
"""
from __future__ import annotations

import glob
import json
import logging
import os
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


async def _apply_set_registry(
    plan: ProtonLaunchPlan, args: dict[str, Any], install_path: str,
) -> None:
    """Apply one ``setRegistry`` action via ``reg.exe add``."""
    root = _ROOT_MAP.get(args.get("root", ""), args.get("root", ""))
    subkey = args.get("subkey", "")
    if not root or not subkey:
        return
    value_data = args.get("valueData", "")
    if isinstance(value_data, str):
        value_data = value_data.replace("{app}", _win_path(install_path))
    reg_args = ["add", f"{root}\\{subkey}", "/f"]
    value_name = args.get("valueName", "")
    if value_name:
        reg_type = _TYPE_MAP.get(str(args.get("valueType", "string")).lower(), "REG_SZ")
        reg_args += ["/v", value_name, "/t", reg_type, "/d", str(value_data)]
    await run_wine(plan, "reg.exe", reg_args)


async def apply_script_registry(
    plan: ProtonLaunchPlan, game_id: str, install_path: str,
) -> None:
    """Apply ``goggame-*.script`` setRegistry actions to the prefix."""
    scripts = glob.glob(os.path.join(install_path, f"goggame-{game_id}.script"))
    if not scripts:
        scripts = glob.glob(os.path.join(install_path, "goggame-*.script"))
    if not scripts:
        return
    for script_file in scripts:
        try:
            data = json.loads(Path(script_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("[gog_setup] script parse failed %s: %s", script_file, e)
            continue
        actions = data.get("actions", []) if isinstance(data, dict) else []
        logger.info(
            "[gog_setup] %s: %d script action(s)",
            os.path.basename(script_file), len(actions),
        )
        for action in actions:
            install = action.get("install", {}) if isinstance(action, dict) else {}
            if install.get("action") == "setRegistry":
                await _apply_set_registry(
                    plan, install.get("arguments", {}) or {}, install_path,
                )
            # 'Execute' actions intentionally skipped (headless hang risk).
