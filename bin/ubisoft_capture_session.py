#!/usr/bin/env python3
"""
Capture UPC session token from a game prefix after game exit.

Reads restore_session from the prefix's settings.yml and saves it
for reuse across all prefixes. Only saves if UPC wrote a different
token than what we injected (i.e. UPC refreshed its own token).

Usage:
    python3 ubisoft_capture_session.py <prefix_path>
"""
import json
import os
import re
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")


def get_active_prefix(prefix_path: str) -> str:
    """Detect the active Wine prefix root (may be prefix_path or prefix_path/pfx)."""
    pfx = os.path.join(prefix_path, "pfx")
    if os.path.isdir(pfx) and os.path.isfile(os.path.join(pfx, "system.reg")):
        return pfx
    return prefix_path


def read_restore_session(prefix_path: str) -> str | None:
    """Read restore_session from a prefix's settings.yml."""
    active_prefix = get_active_prefix(prefix_path)

    for user_dir in ["deck", "steamuser"]:
        settings_file = os.path.join(
            active_prefix, "drive_c", "users", user_dir,
            "AppData", "Roaming", "Ubisoft", "Ubisoft Connect", "settings.yml",
        )
        if not os.path.isfile(settings_file):
            continue
        try:
            with open(settings_file) as f:
                content = f.read()
            m = re.search(r'restore_session:\s+"([^"]+)"', content)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def get_api_ticket() -> str:
    """Read the REST API ticket for comparison."""
    if not os.path.isfile(TOKEN_FILE):
        return ""
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        return data.get("ticket", "")
    except Exception:
        return ""


def iter_ubisoft_prefixes():
    """Yield all Ubisoft prefix directories, including the template."""
    if not os.path.isdir(PREFIXES_DIR):
        return
    for entry in sorted(os.listdir(PREFIXES_DIR)):
        prefix_path = os.path.join(PREFIXES_DIR, entry)
        if os.path.isdir(prefix_path):
            yield prefix_path


def write_session_to_prefix(prefix_path: str, token: str) -> None:
    """Write restore_session into a prefix's settings.yml."""
    active_prefix = get_active_prefix(prefix_path)
    user_id = ""
    if os.path.isfile(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            user_id = data.get("userId", "")
        except Exception:
            pass

    settings_dir = os.path.join(
        active_prefix, "drive_c", "users", "deck",
        "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
    )
    os.makedirs(settings_dir, exist_ok=True)
    settings_file = os.path.join(settings_dir, "settings.yml")

    config = (
        "user:\n"
        "  remember_me: true\n"
        f'  restore_session: "{token}"\n'
        f'  userId: "{user_id}"\n'
    )
    with open(settings_file, "w") as f:
        f.write(config)


def capture_session(prefix_path: str) -> bool:
    """Capture UPC session token from the game prefix."""
    token = read_restore_session(prefix_path)
    if not token:
        print("[capture] No restore_session found in prefix")
        return False

    # Only save if different from API ticket (UPC wrote its own token)
    api_ticket = get_api_ticket()
    if token == api_ticket:
        print("[capture] Token matches API ticket, no UPC-native token to capture")
        return False

    # Save to session file
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(UPC_SESSION_FILE, "w") as f:
            f.write(token)
        print(f"[capture] Saved UPC session token ({len(token)} chars)")
    except Exception as e:
        print(f"[capture] Failed to save session token: {e}")
        return False

    updated_prefixes = 0
    for target_prefix in iter_ubisoft_prefixes() or []:
        try:
            write_session_to_prefix(target_prefix, token)
            updated_prefixes += 1
        except Exception as e:
            print(f"[capture] Failed to update prefix {target_prefix}: {e}")

    if updated_prefixes:
        print(f"[capture] Updated {updated_prefixes} Ubisoft prefixes with new token")

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <prefix_path>", file=sys.stderr)
        sys.exit(1)

    prefix = sys.argv[1]
    if not os.path.isdir(prefix):
        print(f"[capture] Prefix not found: {prefix}")
        sys.exit(1)

    capture_session(prefix)
