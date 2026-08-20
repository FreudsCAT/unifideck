"""Building a wrapper-store session on disk, for tests.

tests/unit/_wine_session.py

Shared because a Battle.net session is **not** just files: the login token is
a Wine registry key (``Software\\Blizzard Entertainment\\Battle.net\\
UnifiedAuth``), which the client's own log confirms —
``DeleteToken(): Deleting registry token``. A fixture that writes only files
builds a prefix that looks signed in and is not, which is exactly the mistake
that shipped once and produced ``ERROR_TOKEN_NOT_FOUND (49)`` on the device.

Naming follows ``_repo_root.py``: leading underscore so pytest does not
collect it as a test module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# drive_c-relative, matching ``wrapper_session.SPECS["battlenet"]``.
VAULT = "users/steamuser/AppData/Local/Battle.net/Account/309859116/account.db"
LEDGER = "users/steamuser/AppData/Local/Battle.net/CachedData.db"
COOKIES = "users/steamuser/AppData/Local/Battle.net/BrowserCaches/common/Cookies"
CONFIG = "users/steamuser/AppData/Roaming/Battle.net/Battle.net.config"

_REG_KEY = "Software\\\\Blizzard Entertainment\\\\Battle.net"

# A section the transplant must never disturb — the locale ``language_setup``
# writes, and a stand-in for every per-prefix fact ``user.reg`` carries.
CANARY_SECTION = "[Control Panel\\\\International]"


def write_file(prefix: Path, rel: str, data: bytes, *, mtime: float | None = None) -> Path:
    """Write one drive_c-relative file, optionally pinning its mtime."""
    path = prefix / "drive_c" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def write_registry(
    prefix: Path, *, stamp: int, token: str = "tok", canary: bool = True,
) -> Path:
    """A ``user.reg`` holding the Battle.net session keys.

    ``stamp`` is Wine's per-section last-write time in seconds — the rotation
    clock the session ordering reads.
    """
    reg = prefix / "pfx" / "user.reg"
    reg.parent.mkdir(parents=True, exist_ok=True)
    parts = ["WINE REGISTRY Version 2\n\n"]
    if canary:
        parts.append(
            f"{CANARY_SECTION} 1000\n#time=1d0\n\"Locale\"=\"00000409\"\n\n",
        )
    for key in ("UnifiedAuth", "EncryptionKey", "Identity"):
        parts.append(
            f"[{_REG_KEY}\\\\{key}] {stamp}\n"
            f"#time=1dd0\n"
            f'"1001"=hex:01,02,03  ;{token}-{key}\n\n',
        )
    # A sibling key that must stay put: it carries per-game subkeys.
    parts.append(
        f"[{_REG_KEY}\\\\Launch Options\\\\OSI] {stamp}\n"
        f'#time=1dd0\n"URI_TOKEN"="per-game"\n\n',
    )
    reg.write_text("".join(parts), encoding="utf-8")
    return reg


def make_session(
    prefix: Path,
    *,
    mtime: float = 1000.0,
    stamp: int | None = None,
    vault: bytes = b"vault",
    token: str = "tok",
    identity: str | None = "GUID-A",
    registry: bool = True,
) -> Path:
    """A prefix that reads as fully signed in: files *and* registry token."""
    write_file(prefix, VAULT, vault, mtime=mtime)
    write_file(prefix, LEDGER, b"ledger", mtime=mtime)
    write_file(prefix, COOKIES, b"cookies", mtime=mtime)
    if identity is not None:
        write_file(
            prefix, CONFIG,
            json.dumps({"Client": {"GaClientId": identity}}).encode(),
            mtime=mtime,
        )
    if registry:
        write_registry(prefix, stamp=int(stamp if stamp is not None else mtime), token=token)
    return prefix


def token_of(prefix: Path) -> str | None:
    """The token marker currently in ``prefix``'s registry, if any."""
    reg = prefix / "pfx" / "user.reg"
    if not reg.is_file():
        return None
    for line in reg.read_text(encoding="utf-8", errors="replace").splitlines():
        if ";" in line and "-UnifiedAuth" in line:
            return line.split(";", 1)[1].split("-", 1)[0]
    return None
