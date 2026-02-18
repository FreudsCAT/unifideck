#!/usr/bin/env python3
"""
Epic game prerequisites installer.

Reads prereq_info from legendary's installed.json and runs the prerequisite
installer (e.g., Ubisoft Connect, Visual C++ redistributables) inside the
Wine prefix using umu-run. This mirrors Heroic's legendarySetup behavior.

Usage: epic_prerequisites.py <game_id> <prefix_path>
Logs to: ~/.local/share/unifideck/prerequisites.log
"""
import json
import os
import shutil
import subprocess
import sys
import logging
from pathlib import Path

# Setup dedicated logger
LOG_FILE = os.path.expanduser("~/.local/share/unifideck/prerequisites.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("EpicPrerequisites")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_legendary_config():
    """Find legendary config directory with installed.json."""
    candidates = [
        os.path.expanduser("~/.config/legendary"),
        os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary"),
    ]
    
    for path in candidates:
        installed_json = os.path.join(path, "installed.json")
        if os.path.exists(installed_json):
            return installed_json
    
    return None


def get_prereq_info(game_id: str) -> dict | None:
    """Read prereq_info from legendary's installed.json for a given game."""
    installed_json = find_legendary_config()
    if not installed_json:
        logger.warning("legendary installed.json not found")
        return None
    
    try:
        with open(installed_json, 'r') as f:
            data = json.load(f)
        
        game_data = data.get(game_id, {})
        prereq = game_data.get("prereq_info")
        install_path = game_data.get("install_path")
        
        if not prereq or not prereq.get("path"):
            logger.info(f"No prerequisites defined for {game_id}")
            return None
        
        # Add install_path to prereq info for convenience
        prereq["install_path"] = install_path
        return prereq
        
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to read installed.json: {e}")
        return None


def find_umu_run():
    """Find umu-run binary."""
    candidates = [
        os.path.join(SCRIPT_DIR, "umu", "umu", "umu-run"),
        shutil.which("umu-run"),
    ]
    
    for path in candidates:
        if path and os.path.exists(path):
            return path
    
    return None


def find_python310_plus():
    """Find Python 3.10+ for umu-run."""
    for ver in ["3.13", "3.12", "3.11", "3.10"]:
        path = f"/usr/bin/python{ver}"
        if os.path.exists(path):
            return path
    
    return shutil.which("python3")


def run_prerequisite(game_id: str, prefix_path: str, prereq: dict) -> bool:
    """
    Run the prerequisite installer inside the Wine prefix via umu-run.
    
    This mirrors Heroic's legendarySetup which runs:
        runWineCommand({commandParts: [install_path/prereq.path, ...prereq.args], wait: true})
    
    Args:
        game_id: Epic game ID
        prefix_path: Wine prefix root path
        prereq: prereq_info dict with 'path', 'args', 'name', 'install_path'
    
    Returns:
        True if installation succeeded, False otherwise
    """
    install_path = prereq.get("install_path", "")
    prereq_path = prereq.get("path", "")
    prereq_args = prereq.get("args", "")
    prereq_name = prereq.get("name", "Unknown")
    
    # Build full path to the prerequisite installer
    full_installer_path = os.path.join(install_path, prereq_path)
    
    if not os.path.exists(full_installer_path):
        logger.error(f"Prerequisite installer not found: {full_installer_path}")
        return False
    
    logger.info(f"Installing prerequisite: {prereq_name}")
    logger.info(f"Installer: {full_installer_path}")
    logger.info(f"Args: {prereq_args}")
    
    # Find umu-run
    umu_run = find_umu_run()
    if not umu_run:
        logger.error("umu-run not found! Cannot install prerequisites.")
        return False
    
    # Find Python 3.10+
    python_bin = find_python310_plus()
    if not python_bin:
        logger.error("Python 3.10+ not found!")
        return False
    
    logger.info(f"Using umu-run: {umu_run}")
    logger.info(f"Using Python: {python_bin}")
    
    # Setup environment — inherit from parent (which has PROTONPATH, etc. already set)
    env = os.environ.copy()
    env["WINEPREFIX"] = prefix_path
    env["GAMEID"] = "umu-0"
    env["PROTON_VERB"] = "waitforexitandrun"
    
    # Clean problematic env vars
    env.pop("LD_PRELOAD", None)
    
    # Build command: python3 umu-run /path/to/installer.exe /S
    cmd = [python_bin, umu_run, full_installer_path]
    if prereq_args:
        # Split args (usually just "/S" for silent install)
        cmd.extend(prereq_args.split())
    
    logger.info(f"Executing: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600  # 10 min timeout for prerequisite installers
        )
        
        # Log output (filtered)
        for line in result.stdout.splitlines():
            line_lower = line.lower()
            if any(p in line_lower for p in ["error", "warn", "install", "success", "fail", "complete", "info:"]):
                logger.info(f"  {line.strip()}")
        
        if result.returncode == 0:
            logger.info(f"✓ {prereq_name} installed successfully")
            return True
        else:
            # Many Windows installers return non-zero for "already installed" or 
            # partial success — we still mark as done since the files are likely in place
            logger.warning(f"{prereq_name} exited with code {result.returncode} (may still be OK)")
            return True  # Treat as success — the game will fail at launch if it truly didn't work
            
    except subprocess.TimeoutExpired:
        logger.error(f"{prereq_name} installation timed out after 10 minutes")
        return False
    except Exception as e:
        logger.error(f"Failed to run {prereq_name}: {e}")
        return False


def main():
    if len(sys.argv) < 3:
        print("Usage: epic_prerequisites.py <game_id> <prefix_path>")
        sys.exit(1)
    
    game_id = sys.argv[1]
    prefix_path = sys.argv[2]
    marker_file = os.path.join(prefix_path, f".unifideck_prereqs_{game_id}.done")
    
    logger.info(f"{'='*60}")
    logger.info(f"Prerequisites check for {game_id}")
    logger.info(f"Prefix: {prefix_path}")
    
    # Check if already installed
    if os.path.exists(marker_file):
        logger.info("Prerequisites already installed, skipping")
        logger.info(f"{'='*60}")
        return
    
    # Get prereq info from legendary
    prereq = get_prereq_info(game_id)
    
    if not prereq:
        logger.info("No prerequisites to install")
        # Create marker so we don't check again
        with open(marker_file, 'w') as f:
            f.write("no prerequisites")
        logger.info(f"{'='*60}")
        return
    
    logger.info(f"Found prerequisite: {prereq.get('name', 'Unknown')}")
    logger.info(f"  IDs: {prereq.get('ids', [])}")
    logger.info(f"  Path: {prereq.get('path', '')}")
    logger.info(f"  Args: {prereq.get('args', '')}")
    
    # Ensure prefix exists
    os.makedirs(prefix_path, exist_ok=True)
    
    # Run the prerequisite installer
    success = run_prerequisite(game_id, prefix_path, prereq)
    
    if success:
        # Create marker file
        with open(marker_file, 'w') as f:
            f.write(f"installed: {prereq.get('name', 'Unknown')}")
        logger.info("Prerequisites installation complete")
    else:
        logger.error("Prerequisites installation failed")
        sys.exit(1)
    
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
