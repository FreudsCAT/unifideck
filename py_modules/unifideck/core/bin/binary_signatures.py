"""core/bin/binary_signatures.py — SHA256 allowlist for bundled CLI tools.

# OP-07b | core/bin/binary_signatures.py | Depends: (none)

Verifies the integrity of binaries shipped under ``bin/`` against
a hand-maintained allowlist. System binaries resolved via PATH
are out of scope — trusted by the OS package manager.
"""
from __future__ import annotations

import hashlib

# Allowlist of known-good SHA256 hashes for bundled binaries.
# When bumping a bundled binary, compute the new hash with
# ``sha256sum bin/<tool>`` and update here IN THE SAME COMMIT.
# Empty string = no reference hash yet — verify returns None
# so early-dev doesn't break the build.
_KNOWN_HASHES: dict[str, str] = {
    "legendary": "",
    "nile": "",
    "gogdl": "",
}


def compute_sha256(path: str, chunk_size: int = 65536) -> str | None:
    """Return hex SHA256 of ``path`` via streaming 64KiB reads.
    Avoids loading 30MB binaries into RAM. Returns None on any
    OSError so callers treat unreadable + mismatched uniformly.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def verify_bundled_binary(tool_name: str, path: str) -> bool | None:
    """Tri-state check against ``_KNOWN_HASHES``.

    Return ``True`` if match, ``False`` if hash differs (tampered
    or wrong version), ``None`` if no allowlist entry or unreadable.
    Caller decides policy: fail-open in dev, fail-closed in prod.
    Store code should treat False as "do NOT invoke this binary".
    """
    expected = _KNOWN_HASHES.get(tool_name)
    if expected is None:
        return None
    if expected == "":
        return None
    actual = compute_sha256(path)
    if actual is None:
        return None
    return actual == expected
