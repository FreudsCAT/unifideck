"""SHA-256 verification of bundled CLI binaries.

OP-08d3 | py_modules/unifideck/core/bin/binary_signatures.py

Defends against partial / corrupted plugin installs by
hashing each bundled binary on first use and comparing
against a baked-in known-good hash table.

``_KNOWN_HASHES`` is the manifest — populated at build time
by CI when the plugin is packaged. An empty string means
"no reference yet" (still in dev / pre-release), in which
case ``verify_bundled_binary`` returns ``None`` so callers
can decide their own permissive policy (typically: warn but
proceed in dev, refuse in production).

The three-state return (``True`` / ``False`` / ``None``)
captures the three meaningful outcomes:

* ``True``  — hash matches, binary is trustworthy;
* ``False`` — mismatch, binary should be refused (logged at
  ERROR with the security tag);
* ``None``  — couldn't verify (no reference, file missing,
  read error). Caller decides.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_KNOWN_HASHES: dict[str, str] = {
    "legendary": "",
    "nile": "",
    "gogdl": "",
}


def compute_sha256(path: str, chunk_size: int = 65536) -> str | None:
    """Hash a file's contents with SHA-256, streaming in chunks.

    Streaming (not whole-file read) keeps memory bounded
    for large binaries (Legendary is 10 MB+). The default
    64 KB chunk balances syscall overhead against memory.

    Read errors (file missing, permission denied) are
    caught and logged at WARN; returns ``None`` so the
    caller can distinguish "couldn't compute" from a
    legitimate hash value.

    Args:
        path: filesystem path to hash.
        chunk_size: read chunk size in bytes (default
            64 KB).

    Returns:
        Hex-encoded SHA-256 digest as string, or ``None``
        on read error.
    """
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        logger.warning(
            "[binary_signatures] Cannot read %s: %s",
            path,
            e,
        )
        return None


def verify_bundled_binary(tool_name: str, path: str) -> bool | None:
    """Compare a bundled binary's hash against the known-good manifest.

    Four-arm dispatch:

    1. **No reference hash** (empty string in
       ``_KNOWN_HASHES`` — dev build) → log at DEBUG,
       return ``None``. Caller policy.
    2. **File missing**     → log at WARN, return
       ``None``.
    3. **Hash mismatch**    → log at ERROR with the
       security tag, return ``False``. Strong signal to
       refuse.
    4. **Hash matches**     → log at INFO, return
       ``True``.

    The ERROR log line is intentionally verbose (includes
    expected vs actual) so an operator investigating a
    suspicious install sees the exact hashes in plugin
    logs.

    Args:
        tool_name: key into ``_KNOWN_HASHES`` (e.g.
            ``"legendary"``).
        path: filesystem path to the binary.

    Returns:
        Three-state result: ``True`` if verified, ``False``
        if mismatch, ``None`` if can't verify.
    """
    expected = _KNOWN_HASHES.get(tool_name, "")
    if not expected:
        logger.debug(
            "[binary_signatures] No reference hash for %s — "
            "returning None (caller decides policy)",
            tool_name,
        )
        return None
    if not Path(path).is_file():
        logger.warning(
            "[binary_signatures] %s not found at %s",
            tool_name,
            path,
        )
        return None
    actual = compute_sha256(path)
    if actual is None:
        return None
    if actual != expected:
        logger.error(
            "[binary_signatures] SECURITY: %s hash mismatch at %s. "
            "Expected %s, got %s. Refusing to trust this binary.",
            tool_name,
            path,
            expected,
            actual,
        )
        return False
    logger.info(
        "[binary_signatures] %s at %s verified (sha256=%s)",
        tool_name,
        path,
        actual,
    )
    return True
