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
import hashlib
import os
import re
import shutil
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
AUTH_PREFIX_DIR = os.path.join(PREFIXES_DIR, ".upc-auth")

# UPC credential files synced alongside the session token.
# These are DPAPI-encrypted — they can only be shared between prefixes that
# share the same Wine MachineGuid (enforced by _ensure_auth_prefix in ubisoft.py).
_UPC_CREDENTIAL_FILES = ("ConnectSecureStorage.dat", "user.dat")
_UPC_LOCAL_SUBDIR = os.path.join("AppData", "Local", "Ubisoft Game Launcher")
_UPC_AUTH_CACHE_ARTIFACTS = (
    "settings.yaml",
    os.path.join("cache", "configuration"),
    os.path.join("cache", "settings"),
    os.path.join("cache", "ulcf"),
    os.path.join("cache", "http2", "Default", "Network"),
    os.path.join("cache", "http2", "Default", "Local Storage"),
    os.path.join("cache", "http2", "Default", "IndexedDB"),
    os.path.join("cache", "http2", "Default", "Preferences"),
    os.path.join("cache", "http2", "Default", "Session Storage"),
    os.path.join("cache", "ownership"),
)


_WINE_SYSTEM_USERS = {"Public", "All Users", "Default", "Default User"}


def iter_prefix_user_homes(prefix_path: str, pfx_first: bool = False):
    """Yield user_home paths for all real user dirs across both layouts.

    When pfx_first is True, yield pfx/ layout before bare drive_c/ layout.
    UPC reads from pfx/ so this ensures the freshest files are found first.
    """
    roots = [prefix_path, os.path.join(prefix_path, "pfx")]
    if pfx_first:
        roots = list(reversed(roots))
    for prefix_root in roots:
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


def _find_best_credential_source():
    """Find the prefix with the freshest ConnectSecureStorage.dat to use as credential source.

    Prefers the auth prefix (.upc-auth), checking pfx/ layout first since that's
    where UPC writes fresh credentials.  Falls back to whichever prefix has the
    most recently modified ConnectSecureStorage.dat.
    """
    best_path = None
    best_mtime = 0

    # Check auth prefix first (preferred source) — pfx/ layout first
    for user_home in iter_prefix_user_homes(AUTH_PREFIX_DIR, pfx_first=True):
        css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
        if os.path.isfile(css) and os.path.getsize(css) > 10:
            return AUTH_PREFIX_DIR  # Always prefer auth if it has any valid CSS

    # Fall back: scan all prefixes for most recently modified credentials
    if os.path.isdir(PREFIXES_DIR):
        for entry in os.listdir(PREFIXES_DIR):
            prefix = os.path.join(PREFIXES_DIR, entry)
            if not os.path.isdir(prefix):
                continue
            for user_home in iter_prefix_user_homes(prefix, pfx_first=True):
                css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
                if os.path.isfile(css) and os.path.getsize(css) > 10:
                    mtime = os.path.getmtime(css)
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_path = prefix
                    break  # Use first valid user_home per prefix

    return best_path


def _hash_upc_artifact(path: str) -> str:
    """Build a stable content hash for a file or directory."""
    digest = hashlib.sha256()

    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            files.sort()
            for name in files:
                file_path = os.path.join(root, name)
                rel_path = os.path.relpath(file_path, path)
                digest.update(rel_path.encode("utf-8"))
                with open(file_path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        digest.update(chunk)
    elif os.path.isfile(path):
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)

    return digest.hexdigest()


def _collect_auth_artifact_sources(source_prefix: str) -> dict:
    """Collect auth-adjacent cache/config artifacts from the source prefix."""
    source_artifacts = {}
    for user_home in iter_prefix_user_homes(source_prefix):
        local_root = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
        for rel_path in _UPC_AUTH_CACHE_ARTIFACTS:
            if rel_path in source_artifacts:
                continue
            src = os.path.join(local_root, rel_path)
            if os.path.isdir(src) or os.path.isfile(src):
                source_artifacts[rel_path] = src
    return source_artifacts


def sync_credentials(prefix_path: str) -> bool:
    """Copy UPC credential files from the auth prefix into the target prefix.

    Copies ConnectSecureStorage.dat and user.dat into all user homes
    (prioritizing pfx/ layout) so UPC can validate the session token.
    """
    source_prefix = _find_best_credential_source()
    if not source_prefix:
        print("[inject] No credential source found, skipping credential sync")
        return False

    if os.path.realpath(source_prefix) == os.path.realpath(prefix_path):
        return True  # Target is the source

    # Collect source credential files — prefer pfx/ layout (where UPC writes)
    source_files = {}  # filename -> source_path
    for user_home in iter_prefix_user_homes(source_prefix, pfx_first=True):
        for fname in _UPC_CREDENTIAL_FILES:
            if fname in source_files:
                continue
            src = os.path.join(user_home, _UPC_LOCAL_SUBDIR, fname)
            if os.path.isfile(src) and os.path.getsize(src) > 100:
                source_files[fname] = src

    if not source_files:
        print("[inject] No valid credential files in source prefix")
        return False

    synced = 0
    # Prioritize pfx/ layout to ensure live files are updated first
    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
        target_dir = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
        for fname, src_path in source_files.items():
            dst_path = os.path.join(target_dir, fname)

            # Skip if target already has identical file (by content hash)
            if os.path.isfile(dst_path):
                try:
                    if _hash_upc_artifact(src_path) == _hash_upc_artifact(dst_path):
                        continue
                except Exception:
                    pass

            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            synced += 1
            # Show relative path from prefix for clarity
            rel = os.path.relpath(dst_path, prefix_path)
            print(f"[inject] Synced {fname} → {rel}")

    if synced:
        print(f"[inject] Synced {synced} credential file(s) from {os.path.basename(source_prefix)}")
    return synced > 0


def sync_auth_artifacts(prefix_path: str) -> bool:
    """Copy auth-adjacent cache/config artifacts into the target prefix."""
    source_prefix = _find_best_credential_source()
    if not source_prefix:
        print("[inject] No credential source found, skipping auth artifact sync")
        return False

    if os.path.realpath(source_prefix) == os.path.realpath(prefix_path):
        return True

    source_artifacts = _collect_auth_artifact_sources(source_prefix)
    if not source_artifacts:
        print("[inject] No auth cache artifacts found in source prefix")
        return False

    synced = 0
    for user_home in iter_prefix_user_homes(prefix_path):
        target_root = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
        for rel_path, src_path in source_artifacts.items():
            dst_path = os.path.join(target_root, rel_path)
            if os.path.exists(dst_path):
                try:
                    if _hash_upc_artifact(src_path) == _hash_upc_artifact(dst_path):
                        continue
                except Exception:
                    pass

            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            if os.path.isdir(dst_path):
                shutil.rmtree(dst_path, ignore_errors=True)
            elif os.path.exists(dst_path):
                os.remove(dst_path)

            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
            synced += 1
            rel = os.path.relpath(dst_path, prefix_path)
            print(f"[inject] Synced auth artifact → {rel}")

    if synced:
        print(f"[inject] Synced {synced} auth cache artifact(s) from {os.path.basename(source_prefix)}")
    return synced > 0


def inject_session(prefix_path: str) -> bool:
    """Inject session token and credential files into prefix."""
    # Sync binary credential files first (ConnectSecureStorage.dat, user.dat)
    sync_credentials(prefix_path)
    sync_auth_artifacts(prefix_path)

    token, user_id = read_token()
    if not token:
        print("[inject] No session token available, skipping")
        return False

    config = (
        "user:\n"
        "  remember_me: true\n"
        f'  restore_session: "{token}"\n'
        f'  userId: "{user_id}"\n'
    )

    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
        settings_dir = os.path.join(
            user_home, "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
        )
        settings_file = os.path.join(settings_dir, "settings.yml")

        # Skip if token already matches
        if os.path.isfile(settings_file):
            try:
                with open(settings_file) as f:
                    content = f.read()
                m = re.search(r'restore_session:\s+"([^"]+)"', content)
                if m and m.group(1) == token:
                    continue
            except Exception:
                pass

        os.makedirs(settings_dir, exist_ok=True)
        try:
            with open(settings_file, "w") as f:
                f.write(config)
            # Show relative path for readability
            rel = os.path.relpath(user_home, prefix_path)
            print(f"[inject] Session injected into {rel}")
        except Exception as e:
            print(f"[inject] Failed to write {user_home}: {e}")

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
