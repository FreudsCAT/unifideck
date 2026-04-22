"""core/bin/binary_signatures.py — SHA256 allowlist for bundled CLI tools.

# OP-07b | core/bin/binary_signatures.py | Depends: (none)

Verifies the integrity of binaries shipped under ``bin/`` against
a hand-maintained allowlist. System binaries resolved via PATH
are out of scope — trusted by the OS package manager.
"""
from __future__ import annotations

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
    raise NotImplementedError("OP-07b: implement streaming SHA256 using hashlib")


def verify_bundled_binary(tool_name: str, path: str) -> bool | None:
    """Tri-state check against ``_KNOWN_HASHES``.

    Return ``True`` if match, ``False`` if hash differs (tampered
    or wrong version), ``None`` if no allowlist entry or unreadable.
    Caller decides policy: fail-open in dev, fail-closed in prod.
    Store code should treat False as "do NOT invoke this binary".
    """
    raise NotImplementedError("OP-07b: implement against _KNOWN_HASHES allowlist")
