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
import hashlib
import os
import re
import shutil
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")
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


def read_restore_session(prefix_path: str) -> str | None:
    """Read restore_session from a prefix's settings.yml."""
    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
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
    for user_home in iter_prefix_user_homes(source_prefix, pfx_first=True):
        local_root = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
        for rel_path in _UPC_AUTH_CACHE_ARTIFACTS:
            if rel_path in source_artifacts:
                continue
            src = os.path.join(local_root, rel_path)
            if os.path.isdir(src) or os.path.isfile(src):
                source_artifacts[rel_path] = src
    return source_artifacts


def propagate_credentials(source_prefix: str) -> int:
    """Copy UPC credential files from source prefix to all other Ubisoft prefixes.

    Syncs ConnectSecureStorage.dat and user.dat so all prefixes share
    the same encrypted credential store. Returns number of files synced.
    """
    # Collect source credential files
    source_files = {}  # filename -> source_path
    for user_home in iter_prefix_user_homes(source_prefix, pfx_first=True):
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

                # Skip if already identical (by content hash)
                if os.path.isfile(dst_path):
                    try:
                        if _hash_upc_artifact(src_path) == _hash_upc_artifact(dst_path):
                            continue
                    except Exception:
                        pass

                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                total_synced += 1

    return total_synced


def propagate_auth_artifacts(source_prefix: str) -> int:
    """Copy auth-adjacent cache/config artifacts to every other Ubisoft prefix."""
    source_artifacts = _collect_auth_artifact_sources(source_prefix)
    if not source_artifacts:
        print("[capture] No auth cache artifacts to propagate")
        return 0

    source_real = os.path.realpath(source_prefix)
    total_synced = 0

    for target_prefix in iter_ubisoft_prefixes() or []:
        if os.path.realpath(target_prefix) == source_real:
            continue

        for user_home in iter_prefix_user_homes(target_prefix):
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
                total_synced += 1

    return total_synced


def _check_prefix_health(prefix_path: str) -> bool:
    """Check if a prefix has a healthy, logged-in session.

    A prefix is healthy if it has a non-empty restore_session in settings.yml
    AND a non-empty ConnectSecureStorage.dat.
    """
    token = read_restore_session(prefix_path)
    if not token or len(token) < 50:
        return False

    has_credentials = False
    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
        css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
        if os.path.isfile(css) and os.path.getsize(css) > 100:
            has_credentials = True
            break
    
    return has_credentials


def capture_session(prefix_path: str) -> bool:
    """Capture UPC session token and credentials from a prefix.

    Implements a "Healthy-Source Reconciliation" policy:
    - If the prefix is .upc-auth, it always allowed to capture.
    - If it's a game prefix, it's only allowed to capture if it's "Healthy"
      (has both a token and credentials).
    - If allowed, it propagates its state (token + binaries) to every other prefix.
    """
    is_auth_prefix = os.path.realpath(prefix_path) == os.path.realpath(AUTH_PREFIX_DIR)
    
    if not is_auth_prefix:
        if not _check_prefix_health(prefix_path):
            print(f"[capture] Skipping session capture: {os.path.basename(prefix_path)} is logged out or unhealthy")
            return True
        print(f"[capture] Healthy session detected in {os.path.basename(prefix_path)}; reconciling globally")

    token = read_restore_session(prefix_path)
    if not token:
        print("[capture] No restore_session found in prefix")
        return False

    # Save to session file if it's a new UPC-native token OR if the global file is missing
    api_ticket = get_api_ticket()
    if token != api_ticket or not os.path.isfile(UPC_SESSION_FILE):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(UPC_SESSION_FILE, "w") as f:
                f.write(token)
            print(f"[capture] Saved UPC session token ({len(token)} chars)")

            # Propagate the new token to all other prefixes
            updated_prefixes = 0
            for target_prefix in iter_ubisoft_prefixes() or []:
                try:
                    write_session_to_prefix(target_prefix, token)
                    updated_prefixes += 1
                except Exception as e:
                    print(f"[capture] Failed to update prefix {target_prefix}: {e}")
            if updated_prefixes:
                print(f"[capture] Updated {updated_prefixes} Ubisoft prefixes with new token")
        except Exception as e:
            print(f"[capture] Failed to save session token: {e}")
            return False
    else:
        print("[capture] Token matches API ticket, skipping token file write")

    # Propagate binary credential files (ConnectSecureStorage.dat, user.dat)
    # ALWAYS do this if we are allowed to capture, to ensure the 'matched pair'
    # of token+credentials is reconciled across the system.
    try:
        cred_count = propagate_credentials(prefix_path)
        if cred_count:
            print(f"[capture] Propagated {cred_count} credential file(s) to other prefixes")
    except Exception as e:
        print(f"[capture] Credential propagation failed: {e}")

    try:
        artifact_count = propagate_auth_artifacts(prefix_path)
        if artifact_count:
            print(f"[capture] Propagated {artifact_count} auth cache artifact(s) to other prefixes")
    except Exception as e:
        print(f"[capture] Auth artifact propagation failed: {e}")

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
