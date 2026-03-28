#!/usr/bin/env python3
"""
Capture UPC credentials from a game prefix after game exit.

Detects ConnectSecureStorage.dat (DPAPI-encrypted credential store)
and propagates it to all other Ubisoft prefixes.

Usage:
    python3 ubisoft_capture_session.py <prefix_path>
"""
import hashlib
import os
import re
import shutil
import sys

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")
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


def read_machine_guid(prefix_path: str) -> str:
    """Read Wine MachineGuid from a prefix's system.reg."""
    for reg in [
        os.path.join(prefix_path, "system.reg"),
        os.path.join(prefix_path, "pfx", "system.reg"),
    ]:
        if not os.path.isfile(reg):
            continue
        try:
            with open(reg, "r", encoding="utf-8", errors="ignore") as f:
                m = re.search(r'"MachineGuid"="([^"]+)"', f.read())
                if m:
                    return m.group(1)
        except Exception:
            pass
    return ""


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


def has_valid_credentials(prefix_path: str) -> bool:
    """Check if a prefix has valid ConnectSecureStorage.dat (>100 bytes)."""
    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
        css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
        if os.path.isfile(css) and os.path.getsize(css) > 100:
            return True
    return False


def get_credential_mtime(prefix_path: str) -> float:
    """Get most recent mtime of ConnectSecureStorage.dat in a prefix."""
    best = 0.0
    for user_home in iter_prefix_user_homes(prefix_path, pfx_first=True):
        css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
        if os.path.isfile(css) and os.path.getsize(css) > 100:
            mtime = os.path.getmtime(css)
            if mtime > best:
                best = mtime
    return best


def iter_ubisoft_prefixes():
    """Yield all Ubisoft prefix directories, including the template."""
    if not os.path.isdir(PREFIXES_DIR):
        return
    for entry in sorted(os.listdir(PREFIXES_DIR)):
        prefix_path = os.path.join(PREFIXES_DIR, entry)
        if os.path.isdir(prefix_path):
            yield prefix_path


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
    source_guid = read_machine_guid(source_prefix)
    total_synced = 0

    for target_prefix in iter_ubisoft_prefixes():
        if os.path.realpath(target_prefix) == source_real:
            continue  # Skip source prefix

        # DPAPI-encrypted files require matching MachineGuid
        target_guid = read_machine_guid(target_prefix)
        if source_guid and target_guid and source_guid != target_guid:
            continue  # Skip this target, DPAPI keys won't match

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


def capture_session(prefix_path: str) -> bool:
    """Capture UPC credentials from a prefix and propagate to all others.

    UPC stores auth in DPAPI-encrypted files (ConnectSecureStorage.dat,
    user.dat), not in settings.yml tokens. Detecting valid credential
    files is the auth signal.
    """
    if not has_valid_credentials(prefix_path):
        print("[capture] No valid ConnectSecureStorage.dat in prefix")
        return False

    new_mtime = get_credential_mtime(prefix_path)
    if not new_mtime:
        print("[capture] Could not read credential mtime")
        return False

    # Check if credentials changed since last capture
    stored_mtime = 0.0
    if os.path.isfile(UPC_SESSION_FILE):
        try:
            with open(UPC_SESSION_FILE) as f:
                content = f.read().strip()
            if content.startswith("credential_mtime:"):
                stored_mtime = float(content.split(":", 1)[1])
        except Exception:
            pass

    credentials_changed = new_mtime > stored_mtime

    if credentials_changed:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(UPC_SESSION_FILE, "w") as f:
                f.write(f"credential_mtime:{new_mtime}\n")
            print(f"[capture] Wrote credential marker (mtime={new_mtime})")
        except Exception as e:
            print(f"[capture] Failed to write credential marker: {e}")

    # Propagate credentials and auth artifacts to all other prefixes
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
