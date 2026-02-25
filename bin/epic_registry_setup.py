#!/usr/bin/env python3
"""
Injects Epic and Ubisoft registry keys into a Wine/Proton prefix so that
Ubisoft Connect recognises already-downloaded games without prompting
for re-installation.

Critical: Registry keys MUST be written into the pfx/ subdirectory,
because that is where Proton's wineserver reads from at runtime.
"""
import sys
import os
import json
import logging
import subprocess

log_file = os.path.expanduser("~/.local/share/unifideck/registry_setup.log")
logging.basicConfig(filename=log_file, level=logging.INFO,
                    format='%(asctime)s %(message)s')


def normalize_prefix_root(prefix_path: str) -> str:
    """Canonicalize prefix root, stripping trailing /pfx segments."""
    p = os.path.normpath(prefix_path)
    while p.endswith(os.sep + "pfx"):
        p = os.path.normpath(p[:-(len(os.sep) + 3)])
    return p


def get_active_wineprefix(prefix_root: str) -> str:
    """Resolve active Wine prefix without creating recursive pfx directories."""
    pfx_path = os.path.join(prefix_root, "pfx")
    try:
        if os.path.islink(pfx_path) and os.path.realpath(pfx_path) == os.path.realpath(prefix_root):
            return prefix_root
    except OSError:
        pass

    if os.path.isfile(os.path.join(pfx_path, "system.reg")):
        return pfx_path
    if os.path.isfile(os.path.join(prefix_root, "system.reg")):
        return prefix_root
    return prefix_root


def setup_registry(game_id, prefix_path, legendary_config):
    prefix_root = normalize_prefix_root(prefix_path)
    installed_json = os.path.join(legendary_config, "installed.json")
    if not os.path.exists(installed_json):
        logging.error(f"installed.json not found at {installed_json}")
        return False

    with open(installed_json) as f:
        data = json.load(f)

    app = data.get(game_id)
    if not app:
        logging.error(f"Game {game_id} not in installed.json")
        return False

    install_path = app.get("install_path")
    if not install_path:
        logging.error("No install_path for game")
        return False

    # Convert Linux path → Wine Z: drive path
    win_path = f"Z:{install_path.replace('/', '\\\\')}"
    if not win_path.endswith("\\\\"):
        win_path += "\\\\"

    # Extract UplayId from launch parameters (handles spaces like -UplayId= 3353)
    import re
    uplay_id = None
    params = app.get("launch_parameters", "")
    m = re.search(r'-UplayId=\s*(\d+)', params)
    if m:
        uplay_id = m.group(1)

    # Locate Proton's wine binary
    proton_path = os.environ.get("PROTONPATH", "")
    wine_bin = os.path.join(proton_path, "files", "bin", "wine") if proton_path else None
    if not wine_bin or not os.path.exists(wine_bin):
        logging.error(f"Wine binary not found at {wine_bin}")
        return False

    # Write to active runtime prefix (pfx layout when available, root for legacy/symlink layouts)
    active_prefix = get_active_wineprefix(prefix_root)
    env = os.environ.copy()
    env["WINEPREFIX"] = active_prefix

    cmds = [
        # Epic Games registry
        [wine_bin, "reg", "add",
         "HKEY_LOCAL_MACHINE\\Software\\Epic Games\\EpicGamesLauncher",
         "/v", "AppDataPath", "/t", "REG_SZ",
         "/d", "C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\", "/f"],
        [wine_bin, "reg", "add",
         f"HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Epic Games\\EpicGamesLauncher\\Manifests\\{game_id}",
         "/v", "InstallLocation", "/t", "REG_SZ", "/d", win_path, "/f"],
        [wine_bin, "reg", "add",
         f"HKEY_CURRENT_USER\\Software\\Epic Games\\EpicGamesLauncher\\Manifests\\{game_id}",
         "/v", "InstallLocation", "/t", "REG_SZ", "/d", win_path, "/f"],
    ]

    # Ubisoft-specific keys
    if uplay_id:
        cmds.extend([
            [wine_bin, "reg", "add",
             f"HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\{uplay_id}",
             "/v", "InstallDir", "/t", "REG_SZ", "/d", win_path, "/f"],
            [wine_bin, "reg", "add",
             f"HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\{uplay_id}",
             "/v", "Language", "/t", "REG_SZ", "/d", "en-US", "/f"],
        ])

    ok = True
    for cmd in cmds:
        result = subprocess.run(cmd, env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logging.error(f"reg add failed: {result.stderr.strip()}")
            ok = False

    # Kill wineserver spawned by 'wine reg add' commands to prevent it from
    # interfering with the subsequent umu-run launch (socket contention,
    # registry file locking, stale wineserver in prefix)
    wineserver_bin = os.path.join(os.path.dirname(wine_bin), "wineserver")
    if os.path.exists(wineserver_bin):
        try:
            subprocess.run([wineserver_bin, "--kill"], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            logging.info("Killed stale wineserver after registry setup")
        except Exception:
            pass

    logging.info(f"Registry setup for {game_id} (uplay={uplay_id}): {'OK' if ok else 'PARTIAL'}")
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: epic_registry_setup.py <game_id> <prefix_path> <legendary_config>")
        sys.exit(1)
    success = setup_registry(sys.argv[1], sys.argv[2], sys.argv[3])
    sys.exit(0 if success else 1)
