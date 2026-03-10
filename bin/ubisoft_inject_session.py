#!/usr/bin/env python3
"""
Inject UPC session token into a game prefix before launch.

Writes restore_session, remember_me, and userId into UPC's settings.yml
so no login prompt appears when launching a game.

Prefers the UPC-native session token (captured after a real UPC login)
over the REST API ticket.

Usage:
    python3 ubisoft_inject_session.py <prefix_path>
"""
import json
import os
import re
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")


def get_active_prefix(prefix_path: str) -> str:
    """Detect the active Wine prefix root (may be prefix_path or prefix_path/pfx)."""
    pfx = os.path.join(prefix_path, "pfx")
    if os.path.isdir(pfx) and os.path.isfile(os.path.join(pfx, "system.reg")):
        return pfx
    return prefix_path


def read_token() -> tuple:
    """Read session token and userId. Returns (token, user_id) or (None, None)."""
    # Prefer UPC-native session token
    if os.path.isfile(UPC_SESSION_FILE):
        try:
            with open(UPC_SESSION_FILE) as f:
                token = f.read().strip()
            if token:
                # Get userId from API token file
                user_id = ""
                if os.path.isfile(TOKEN_FILE):
                    try:
                        with open(TOKEN_FILE) as f:
                            data = json.load(f)
                        user_id = data.get("userId", "")
                    except Exception:
                        pass
                return token, user_id
        except Exception:
            pass

    # Fall back to API ticket
    if os.path.isfile(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            ticket = data.get("ticket", "")
            user_id = data.get("userId", "")
            if ticket:
                return ticket, user_id
        except Exception:
            pass

    return None, None


def inject_session(prefix_path: str) -> bool:
    """Inject session token into prefix's UPC settings.yml."""
    token, user_id = read_token()
    if not token:
        print("[inject] No session token available, skipping")
        return False

    active_prefix = get_active_prefix(prefix_path)

    # Check both user directories
    for user_dir in ["deck", "steamuser"]:
        settings_dir = os.path.join(
            active_prefix, "drive_c", "users", user_dir,
            "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
        )
        settings_file = os.path.join(settings_dir, "settings.yml")

        # Skip if token already matches (avoid overwriting UPC-refreshed token)
        if os.path.isfile(settings_file):
            try:
                with open(settings_file) as f:
                    content = f.read()
                m = re.search(r'restore_session:\s+"([^"]+)"', content)
                if m and m.group(1) == token:
                    print(f"[inject] {user_dir}: token already matches, skipping")
                    continue
            except Exception:
                pass

        if not os.path.isdir(settings_dir):
            # Only create for "deck" user dir (primary)
            if user_dir != "deck":
                continue
            os.makedirs(settings_dir, exist_ok=True)

        config = (
            "user:\n"
            "  remember_me: true\n"
            f'  restore_session: "{token}"\n'
            f'  userId: "{user_id}"\n'
        )

        try:
            # Preserve non-user settings if file exists
            if os.path.isfile(settings_file):
                with open(settings_file) as f:
                    existing = f.read()
                if "user:" in existing:
                    result = re.sub(
                        r"user:.*?(?=\n\w|\Z)",
                        config.rstrip("\n"),
                        existing,
                        flags=re.DOTALL,
                    )
                    with open(settings_file, "w") as f:
                        f.write(result)
                else:
                    with open(settings_file, "a") as f:
                        f.write("\n" + config)
            else:
                with open(settings_file, "w") as f:
                    f.write(config)

            print(f"[inject] Session injected into {user_dir}/settings.yml")
        except Exception as e:
            print(f"[inject] Failed to write {user_dir}/settings.yml: {e}")

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <prefix_path>", file=sys.stderr)
        sys.exit(1)

    prefix = sys.argv[1]
    if not os.path.isdir(prefix):
        print(f"[inject] Prefix not found: {prefix}")
        sys.exit(1)

    inject_session(prefix)
