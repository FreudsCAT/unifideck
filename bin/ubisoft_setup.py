#!/usr/bin/env python3
"""
Ubisoft per-game prefix bootstrap script.

Ensures a Wine prefix contains a working upc.exe installation before
game launch or download. Uses template cloning (fast, ~30s) when a
template exists, or fresh installer (slow, ~5-10 min) on first run.

Also injects:
  - UPC auth session (so no login prompt appears)
  - Registry keys for game install path and language
  - Bootstrap completion marker

Usage:
    python3 ubisoft_setup.py <space_id> <prefix_path> [--install-path <path>]

Reference: docs/ubisoft-store-spec.md §6 (Game Installation)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# Constants
# ============================================================================

DATA_DIR = Path.home() / ".local" / "share" / "unifideck"
PREFIXES_DIR = DATA_DIR / "prefixes" / "ubisoft"
TEMPLATE_DIR = PREFIXES_DIR / ".template"
INSTALLER_CACHE_DIR = DATA_DIR / "ubisoft_installer_cache"
INSTALLER_FILENAME = "UbisoftConnectInstaller.exe"
INSTALLER_URL = (
    "https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe"
)
ID_MAP_FILE = DATA_DIR / "ubisoft_id_map.json"
TOKEN_FILE = DATA_DIR / "ubisoft_token.json"
UPC_SESSION_FILE = DATA_DIR / "ubisoft_upc_session.txt"
SETTINGS_FILE = DATA_DIR / "settings.json"
BOOTSTRAP_MARKER = "unifideck_ubisoft_bootstrap.marker"
AUTH_PREFIX_DIR = PREFIXES_DIR / ".upc-auth"

# UPC paths within a Wine prefix (may be under pfx/ for Proton)
UPC_RELATIVE = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"

# Plugin directory (for umu-run)
PLUGIN_DIR = Path.home() / "homebrew" / "plugins" / "Unifideck"
UMU_RUN = PLUGIN_DIR / "bin" / "umu" / "umu" / "umu-run"

LOG_FILE = DATA_DIR / "ubisoft_setup.log"


# ============================================================================
# Logging
# ============================================================================

def log(message: str):
    """Log message to file and stdout."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {message}"
    print(log_msg)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(log_msg + "\n")


# ============================================================================
# Prefix detection
# ============================================================================

def find_upc_exe(prefix_path: str) -> str | None:
    """Find upc.exe in the prefix, checking both direct and pfx/ layouts."""
    candidates = [
        os.path.join(prefix_path, UPC_RELATIVE),
        os.path.join(prefix_path, "pfx", UPC_RELATIVE),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def get_active_prefix(prefix_path: str) -> str:
    """
    Detect the active Wine prefix root (may be prefix_path or prefix_path/pfx).

    Proton creates a pfx/ subdirectory; bare Wine uses the root directly.
    """
    pfx = os.path.join(prefix_path, "pfx")
    if os.path.isdir(pfx) and os.path.isfile(os.path.join(pfx, "system.reg")):
        return pfx
    return prefix_path


def is_bootstrapped(prefix_path: str) -> bool:
    """Check if the prefix has already been bootstrapped."""
    marker = os.path.join(prefix_path, BOOTSTRAP_MARKER)
    return os.path.isfile(marker) and find_upc_exe(prefix_path) is not None


def align_machine_guid(prefix_path: str) -> bool:
    """Align this prefix's pfx/system.reg MachineGuid with the auth prefix.

    Proton generates a unique MachineGuid per prefix, but DPAPI-encrypted
    credential files can only be shared when the MachineGuid matches.
    """
    # Find canonical GUID: prefer auth prefix, fall back to template
    canonical = ""
    for src in [str(AUTH_PREFIX_DIR), str(TEMPLATE_DIR)]:
        if not os.path.isdir(src):
            continue
        for reg_rel in ["pfx/system.reg", "system.reg"]:
            reg_path = os.path.join(src, reg_rel)
            if not os.path.isfile(reg_path):
                continue
            try:
                with open(reg_path, "r", errors="ignore") as f:
                    m = re.search(r'"MachineGuid"="([^"]+)"', f.read())
                    if m:
                        canonical = m.group(1)
                        break
            except Exception:
                pass
        if canonical:
            break

    if not canonical:
        return False

    patched = False
    for reg_rel in ["pfx/system.reg", "system.reg"]:
        reg_path = os.path.join(prefix_path, reg_rel)
        if not os.path.isfile(reg_path):
            continue
        try:
            with open(reg_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            m = re.search(r'"MachineGuid"="([^"]+)"', content)
            if m and m.group(1) != canonical:
                new_content = re.sub(
                    r'"MachineGuid"="[^"]+"',
                    f'"MachineGuid"="{canonical}"',
                    content,
                )
                with open(reg_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                log(f"Aligned MachineGuid in {reg_rel}: {m.group(1)[:8]}... → {canonical[:8]}...")
                patched = True
        except Exception as e:
            log(f"Failed to align MachineGuid in {reg_rel}: {e}")

    return patched


# ============================================================================
# Template cloning (Path B — fast)
# ============================================================================

def clone_from_template(prefix_path: str) -> bool:
    """
    Clone the template prefix to a new per-game prefix using rsync.

    Returns True on success.
    """
    if not os.path.isdir(str(TEMPLATE_DIR)):
        log("Template prefix does not exist, cannot clone")
        return False

    template_marker = os.path.join(str(TEMPLATE_DIR), BOOTSTRAP_MARKER)
    if not os.path.isfile(template_marker):
        log("Template prefix missing bootstrap marker, cannot clone")
        return False

    log(f"Cloning template prefix to {prefix_path}...")
    os.makedirs(prefix_path, exist_ok=True)

    try:
        result = subprocess.run(
            ["rsync", "-a", f"{TEMPLATE_DIR}/", f"{prefix_path}/"],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )
        if result.returncode == 0:
            log("Template cloned successfully")
            return True
        else:
            log(f"rsync failed: {result.stderr[:500]}")
            return False
    except subprocess.TimeoutExpired:
        log("rsync clone timed out after 5 minutes")
        return False
    except Exception as e:
        log(f"Clone failed: {e}")
        return False


# ============================================================================
# Fresh install (Path A — slow)
# ============================================================================

def ensure_installer_cached() -> str | None:
    """
    Download UbisoftConnectInstaller.exe if not already cached.

    Returns path to cached installer, or None on failure.
    """
    os.makedirs(str(INSTALLER_CACHE_DIR), exist_ok=True)
    cached = str(INSTALLER_CACHE_DIR / INSTALLER_FILENAME)

    # Check if cached and valid (PE header starts with MZ)
    if os.path.isfile(cached) and os.path.getsize(cached) > 1000:
        try:
            with open(cached, "rb") as f:
                header = f.read(2)
            if header == b"MZ":
                log("Using cached UbisoftConnectInstaller.exe")
                return cached
        except Exception:
            pass

    log(f"Downloading installer from {INSTALLER_URL}...")
    try:
        import urllib.request
        tmp = cached + ".tmp"
        urllib.request.urlretrieve(INSTALLER_URL, tmp)
        os.replace(tmp, cached)
        size_mb = os.path.getsize(cached) / (1024 * 1024)
        log(f"Installer downloaded ({size_mb:.1f} MB)")
        return cached
    except Exception as e:
        log(f"Download failed: {e}")
        return None


def fresh_install(prefix_path: str) -> bool:
    """
    Install upc.exe from scratch using the Ubisoft Connect installer.

    Runs the installer silently with /S flag under umu-run.

    Returns True on success.
    """
    installer = ensure_installer_cached()
    if not installer:
        return False

    # Find umu-run
    umu_run = str(UMU_RUN)
    if not os.path.isfile(umu_run):
        # Fallback
        umu_run = shutil.which("umu-run") or ""
    if not umu_run:
        log("ERROR: umu-run not found")
        return False

    python_bin = shutil.which("python3") or "python3"

    os.makedirs(prefix_path, exist_ok=True)

    env = os.environ.copy()
    env["WINEPREFIX"] = prefix_path
    env["GAMEID"] = "umu-ubisoft-setup"
    env["STORE"] = "ubisoft"
    env["PROTON_VERB"] = "waitforexitandrun"

    log(f"Installing UPC into {prefix_path} (this may take several minutes)...")

    try:
        result = subprocess.run(
            [python_bin, umu_run, installer, "/S"],
            env=env,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min timeout
        )

        if result.returncode != 0:
            log(f"Installer failed (rc={result.returncode}): {result.stderr[:500]}")
            return False

        # Verify upc.exe exists
        if find_upc_exe(prefix_path):
            log("upc.exe installed successfully")
            return True

        log("ERROR: upc.exe not found after installation")
        return False

    except subprocess.TimeoutExpired:
        log("Installer timed out after 15 minutes")
        return False
    except Exception as e:
        log(f"Installation failed: {e}")
        return False


# ============================================================================
# Session injection
# ============================================================================

def inject_upc_session(prefix_path: str) -> bool:
    """
    Pre-authenticate UPC by injecting the stored session token.

    Prefers UPC-native session token (from ubisoft_upc_session.txt) over
    REST API ticket. Writes restore_session + userId into UPC's settings.yml
    so no login prompt appears.
    """
    ticket = ""
    user_id = ""

    # Prefer UPC-native session token
    if os.path.isfile(str(UPC_SESSION_FILE)):
        try:
            with open(str(UPC_SESSION_FILE), "r") as f:
                upc_token = f.read().strip()
            if upc_token:
                ticket = upc_token
                log("Using UPC-native session token")
        except Exception:
            pass

    # Read userId (and fall back to API ticket if no UPC session)
    if os.path.isfile(str(TOKEN_FILE)):
        try:
            with open(str(TOKEN_FILE), "r") as f:
                tokens = json.load(f)
            user_id = tokens.get("userId", "")
            if not ticket:
                ticket = tokens.get("ticket", "")
                if ticket:
                    log("Using API ticket (no UPC session available)")
        except Exception as e:
            log(f"Failed to read tokens: {e}")

    if not ticket:
        log("No session token available, skipping session injection")
        return False

    active_prefix = get_active_prefix(prefix_path)

    # UPC settings path
    settings_dir = os.path.join(
        active_prefix,
        "drive_c", "users", "deck", "AppData", "Roaming",
        "Ubisoft", "Ubisoft Connect",
    )
    os.makedirs(settings_dir, exist_ok=True)
    settings_file = os.path.join(settings_dir, "settings.yml")

    config_lines = [
        "user:",
        "  remember_me: true",
        f'  restore_session: "{ticket}"',
        f'  userId: "{user_id}"',
        "",
    ]

    try:
        # If file exists, try to preserve non-user settings
        if os.path.isfile(settings_file):
            with open(settings_file, "r") as f:
                existing = f.read()
            if "user:" in existing:
                new_section = "\n".join(config_lines)
                result = re.sub(
                    r"user:.*?(?=\n\w|\Z)",
                    new_section,
                    existing,
                    flags=re.DOTALL,
                )
                with open(settings_file, "w") as f:
                    f.write(result)
            else:
                with open(settings_file, "a") as f:
                    f.write("\n" + "\n".join(config_lines))
        else:
            with open(settings_file, "w") as f:
                f.write("\n".join(config_lines))

        log("UPC session injected")
        return True

    except Exception as e:
        log(f"Session injection failed: {e}")
        return False


# ============================================================================
# Registry injection
# ============================================================================

def inject_registry_keys(
    prefix_path: str, install_id: str, install_dir: str, language: str = "en-US"
) -> bool:
    """
    Inject Ubisoft-specific registry keys into the Wine prefix.

    Sets:
      HKLM\\Software\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\{id}\\InstallDir
      HKLM\\Software\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\{id}\\Language
    """
    active_prefix = get_active_prefix(prefix_path)
    system_reg = os.path.join(active_prefix, "system.reg")

    if not os.path.isfile(system_reg):
        log(f"system.reg not found at {system_reg}, prefix may not be initialized")
        return False

    # Convert Linux path to Wine Z: drive path
    wine_path = install_dir
    if install_dir.startswith("/"):
        wine_path = "Z:" + install_dir.replace("/", "\\\\")

    # Extract 2-letter language code
    lang_short = language.split("-")[0] if "-" in language else language

    section = f"[Software\\\\WOW6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\{install_id}]"
    values = [
        f'"InstallDir"="{wine_path}"',
        f'"Language"="{lang_short}"',
    ]

    try:
        with open(system_reg, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if section in content:
            # Update existing section
            sec_start = content.index(section)
            next_sec = re.search(r"\n\[", content[sec_start + len(section):])
            sec_end = (
                sec_start + len(section) + next_sec.start()
                if next_sec
                else len(content)
            )
            sec_body = content[sec_start + len(section): sec_end]

            for val in values:
                key = val.split("=")[0]
                pattern = rf'^{re.escape(key)}="[^"]*"'
                new_body, count = re.subn(pattern, val, sec_body, flags=re.MULTILINE)
                if count > 0:
                    sec_body = new_body
                else:
                    sec_body = sec_body.rstrip("\n") + "\n" + val + "\n"

            content = (
                content[: sec_start + len(section)]
                + sec_body
                + content[sec_end:]
            )
        else:
            # Append new section
            new_section = f"\n{section}\n" + "\n".join(values) + "\n"
            content += new_section

        with open(system_reg, "w", encoding="utf-8") as f:
            f.write(content)

        log(f"Registry keys injected for install_id={install_id}")
        return True

    except Exception as e:
        log(f"Registry injection failed: {e}")
        return False


# ============================================================================
# Main bootstrap flow
# ============================================================================

def bootstrap(space_id: str, prefix_path: str, install_path: str | None = None) -> bool:
    """
    Full bootstrap flow for a per-game Ubisoft prefix.

    1. Skip if already bootstrapped (idempotent)
    2. Clone from template (Path B) or fresh install (Path A)
    3. Inject UPC auth session
    4. Inject registry keys (if install_id is known)
    5. Write bootstrap marker
    """
    log(f"=== Ubisoft Setup: {space_id} ===")
    log(f"Prefix: {prefix_path}")

    # Idempotency check
    if is_bootstrapped(prefix_path):
        log("Prefix already bootstrapped, skipping")
        # Still inject session (tokens may have refreshed)
        inject_upc_session(prefix_path)
        return True

    # Try Path B (template clone) first, fall back to Path A (fresh install)
    success = False
    if os.path.isdir(str(TEMPLATE_DIR)):
        success = clone_from_template(prefix_path)

    if not success:
        success = fresh_install(prefix_path)

    if not success:
        log("ERROR: Bootstrap failed - could not install upc.exe")
        return False

    # If fresh install succeeded and no template exists, create one
    if not os.path.isfile(os.path.join(str(TEMPLATE_DIR), BOOTSTRAP_MARKER)):
        log("Creating template from this prefix for future games...")
        try:
            os.makedirs(str(TEMPLATE_DIR), exist_ok=True)
            subprocess.run(
                ["rsync", "-a", f"{prefix_path}/", f"{TEMPLATE_DIR}/"],
                capture_output=True,
                timeout=300,
            )
            template_marker = os.path.join(str(TEMPLATE_DIR), BOOTSTRAP_MARKER)
            with open(template_marker, "w") as f:
                f.write("template\n")
            log("Template prefix created")
        except Exception as e:
            log(f"WARNING: Template creation failed: {e}")

    # Wait for Wine to fully shut down before modifying registry files.
    # fresh_install/clone may leave wineserver running in the background.
    try:
        env = os.environ.copy()
        env["WINEPREFIX"] = prefix_path
        subprocess.run(["wineserver", "-w"], env=env, timeout=30,
                       capture_output=True)
    except Exception:
        pass  # wineserver may not exist or already exited

    # Align MachineGuid with auth prefix so DPAPI credentials can be shared
    align_machine_guid(prefix_path)

    # Inject auth session
    inject_upc_session(prefix_path)

    # Inject registry keys if we have the install_id
    try:
        if os.path.isfile(str(ID_MAP_FILE)):
            with open(str(ID_MAP_FILE), "r") as f:
                id_map = json.load(f)
            entry = id_map.get(space_id, {})
            install_id = entry.get("install_id", "")
            if install_id and install_path:
                # Get language from settings
                lang = "en-US"
                if os.path.isfile(str(SETTINGS_FILE)):
                    try:
                        with open(str(SETTINGS_FILE), "r") as f:
                            settings = json.load(f)
                        lang = settings.get("language", "en-US")
                        if lang == "auto":
                            lang = "en-US"
                    except Exception:
                        pass
                inject_registry_keys(prefix_path, install_id, install_path, lang)
    except Exception as e:
        log(f"WARNING: Registry injection skipped: {e}")

    # Write bootstrap marker
    marker_path = os.path.join(prefix_path, BOOTSTRAP_MARKER)
    try:
        with open(marker_path, "w") as f:
            f.write(f"game={space_id}\n")
            f.write(f"created={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        log("Bootstrap marker written")
    except Exception as e:
        log(f"WARNING: Could not write bootstrap marker: {e}")

    log("=== Bootstrap complete ===")
    return True


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Ubisoft per-game prefix bootstrap")
    parser.add_argument("space_id", help="Game space_id")
    parser.add_argument("prefix_path", help="Path to per-game Wine prefix")
    parser.add_argument(
        "--install-path", default=None, help="Game install directory (for registry keys)"
    )

    args = parser.parse_args()
    success = bootstrap(args.space_id, args.prefix_path, args.install_path)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
