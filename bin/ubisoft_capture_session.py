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
import shutil
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")

# UPC credential files that must be synced alongside the session token
_UPC_CREDENTIAL_FILES = ("ConnectSecureStorage.dat", "user.dat")
_UPC_LOCAL_SUBDIR = os.path.join("AppData", "Local", "Ubisoft Game Launcher")


_WINE_SYSTEM_USERS = {"Public", "All Users", "Default", "Default User"}


def iter_prefix_user_homes(prefix_path: str):
    """Yield user_home paths for all real user dirs across both layouts."""
    for prefix_root in [prefix_path, os.path.join(prefix_path, "pfx")]:
        users_dir = os.path.join(prefix_root, "drive_c", "users")
        if not os.path.isdir(users_dir):
            continue
        try:
            entries = os.listdir(users_dir)
        except OSError:
            continue
        for entry in entries:
            if entry in _WINE_SYSTEM_USERS:
                continue
            user_home = os.path.join(users_dir, entry)
            if os.path.isdir(user_home):
                yield user_home


def read_restore_session(prefix_path: str) -> str | None:
    """Read restore_session from a prefix's settings.yml."""
    for user_home in iter_prefix_user_homes(prefix_path):
        settings_file = os.path.join(
            user_home, "AppData", "Roaming", "Ubisoft",
            "Ubisoft Connect", "settings.yml",
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
    """Write restore_session into a prefix's settings.yml (both layouts, both users)."""
    user_id = ""
    if os.path.isfile(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            user_id = data.get("userId", "")
        except Exception:
            pass

    config = (
        "user:\n"
        "  remember_me: true\n"
        f'  restore_session: "{token}"\n'
        f'  userId: "{user_id}"\n'
    )

    for user_home in iter_prefix_user_homes(prefix_path):
        settings_dir = os.path.join(
            user_home, "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
        )
        os.makedirs(settings_dir, exist_ok=True)
        settings_file = os.path.join(settings_dir, "settings.yml")
        with open(settings_file, "w") as f:
            f.write(config)


def propagate_credentials(source_prefix: str) -> int:
    """Copy UPC credential files from source prefix to all other Ubisoft prefixes.

    Syncs ConnectSecureStorage.dat and user.dat so all prefixes share
    the same encrypted credential store. Returns number of files synced.
    """
    # Collect source credential files
    source_files = {}  # filename -> source_path
    for user_home in iter_prefix_user_homes(source_prefix):
        for fname in _UPC_CREDENTIAL_FILES:
            if fname in source_files:
                continue
            src = os.path.join(user_home, _UPC_LOCAL_SUBDIR, fname)
            if os.path.isfile(src) and os.path.getsize(src) > 10:
                source_files[fname] = src

    if not source_files:
        print("[capture] No valid credential files to propagate")
        return 0

    source_real = os.path.realpath(source_prefix)
    total_synced = 0

    for target_prefix in iter_ubisoft_prefixes():
        if os.path.realpath(target_prefix) == source_real:
            continue  # Skip source prefix

        for user_home in iter_prefix_user_homes(target_prefix):
            target_dir = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
            for fname, src_path in source_files.items():
                dst_path = os.path.join(target_dir, fname)
                src_size = os.path.getsize(src_path)

                # Skip if already identical
                if os.path.isfile(dst_path) and os.path.getsize(dst_path) == src_size:
                    continue

                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                total_synced += 1

    return total_synced


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

    # Propagate binary credential files (ConnectSecureStorage.dat, user.dat)
    try:
        cred_count = propagate_credentials(prefix_path)
        if cred_count:
            print(f"[capture] Propagated {cred_count} credential file(s) to other prefixes")
    except Exception as e:
        print(f"[capture] Credential propagation failed: {e}")

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
