"""Moving named registry sections between Wine prefixes.

py_modules/unifideck/launcher/wine_registry.py

Some vendor clients keep their login token in the **Wine registry**, not in a
file. Battle.net is one, and its own log says so outright::

    [BNLogin] BattleNetLogin::DeleteToken(): Deleting registry token
    [BNLogin] Login failed. error=ERROR_TOKEN_NOT_FOUND (49)

Measured 2026-08-11, ``user.reg`` in the auth prefix::

    [Software\\\\Blizzard Entertainment\\\\Battle.net\\\\UnifiedAuth]   <- the token
    [Software\\\\Blizzard Entertainment\\\\Battle.net\\\\EncryptionKey] "CacheDatabase"
    [Software\\\\Blizzard Entertainment\\\\Battle.net\\\\Identity]      "Identity"

That is why a whole-prefix ``rsync`` clone opens signed in while copying only
the client's files does not: the rsync carries ``user.reg``, and a file-only
copy leaves the token behind. It also explains the shape of the original
on-device experiment recorded in ``stores/battlenet/prefix/manager.py``,
which named exactly these three — they are registry keys, and looking for
them under ``AppData`` finds nothing.

**Wholesale copying of ``user.reg`` is not an option.** It also holds every
per-prefix fact the prefix depends on: installed game paths, Wine settings,
the locale ``language_setup`` writes. Overwriting a game prefix's registry
with the auth prefix's would break the game it contains. So this module moves
**named sections only**, splicing them into whatever the destination already
has.

Two hazards, both handled by the caller rather than here:

* A live ``wineserver`` owns the registry and rewrites it from memory when it
  exits, so a write underneath one is silently discarded. Callers must check
  the prefix is quiet — see ``registry_is_writable``.
* Reading straight after a client exits can miss the last flush, because
  wineserver saves on a short timer rather than on every change.

The section timestamp in the header (``[Key] 1786332467``) is Wine's own
last-write time for that key, in seconds. It is a better ordering signal than
the file's mtime, which moves for unrelated keys.

Stdlib-only; runs under the SYSTEM python (3.10-3.14).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "user.reg"

# ``[Some\\Key] 1786332467`` — the trailing integer is Wine's last-write time.
_SECTION_RE = re.compile(r"^\[(?P<key>[^\]]+)\](?:\s+(?P<stamp>\d+))?\s*$")


def registry_path(prefix: Path | str) -> Path | None:
    """``user.reg`` for ``prefix``, across both prefix layouts.

    umu creates ``pfx -> .`` as a self-symlink, so the registry can be found
    at either spelling; the naive combine has already broken a recovery path
    for another store.
    """
    root = Path(prefix)
    for candidate in (root / REGISTRY_FILENAME, root / "pfx" / REGISTRY_FILENAME):
        if candidate.is_file():
            return candidate
    return None


def _split_sections(content: str) -> list[tuple[str | None, str]]:
    """Split a .reg file into ``(key, raw_text)`` chunks, preamble first.

    Value data spans continuation lines, so splitting has to be driven by
    section headers rather than by blank lines.
    """
    chunks: list[tuple[str | None, str]] = []
    current_key: str | None = None
    buffer: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _SECTION_RE.match(line.rstrip("\n")) if line.startswith("[") else None
        if match:
            chunks.append((current_key, "".join(buffer)))
            current_key = match.group("key")
            buffer = [line]
        else:
            buffer.append(line)
    chunks.append((current_key, "".join(buffer)))
    return chunks


def _wanted(key: str | None, prefixes: tuple[str, ...]) -> bool:
    return key is not None and any(key.startswith(p) for p in prefixes)


def read_sections(
    prefix: Path | str, key_prefixes: tuple[str, ...],
) -> dict[str, str]:
    """Every section under ``key_prefixes``, as ``{key: raw_text}``."""
    path = registry_path(prefix)
    if path is None:
        return {}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {
        key: text
        for key, text in _split_sections(content)
        if _wanted(key, key_prefixes) and key is not None
    }


def newest_stamp(prefix: Path | str, key_prefixes: tuple[str, ...]) -> int:
    """Wine's newest last-write time across the matching sections.

    Zero when nothing matches, which callers read as "no session here".
    """
    newest = 0
    for text in read_sections(prefix, key_prefixes).values():
        match = _SECTION_RE.match(text.splitlines()[0]) if text else None
        if match and match.group("stamp"):
            newest = max(newest, int(match.group("stamp")))
    return newest


def has_sections(prefix: Path | str, key_prefixes: tuple[str, ...]) -> bool:
    """Whether ``prefix`` holds any section under ``key_prefixes``."""
    return bool(read_sections(prefix, key_prefixes))


def registry_is_writable(prefix: Path | str, live_pids: int) -> bool:
    """False while a wineserver owns the registry.

    A live wineserver holds the registry in memory and rewrites the file when
    it exits, so anything written underneath it is discarded without error —
    the worst kind of failure, because every log line says success.
    """
    if live_pids:
        logger.info(
            "[wine_registry] %s has %d live Wine process(es) — refusing to "
            "write the registry underneath a running wineserver",
            Path(prefix).name, live_pids,
        )
        return False
    return registry_path(prefix) is not None


def _atomic_write(path: Path, content: str) -> bool:
    """Replace ``path`` atomically, matching ``language_setup``'s pattern."""
    handle, tmp_name = tempfile.mkstemp(
        prefix=".reg.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(tmp_name).replace(path)
    except OSError as exc:
        logger.warning("[wine_registry] could not write %s: %s", path, exc)
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink()
        return False
    return True


def merge_sections(prefix: Path | str, sections: dict[str, str]) -> int:
    """Splice ``sections`` into ``prefix``'s registry. Returns how many landed.

    Replaces a section that already exists and appends one that does not.
    Everything else in the file is preserved byte for byte — that is the whole
    point, because the destination's own keys include the installed game's
    paths.
    """
    path = registry_path(prefix)
    if path is None or not sections:
        return 0
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    remaining = dict(sections)
    rebuilt: list[str] = []
    for key, text in _split_sections(content):
        replacement = remaining.pop(key, None) if key is not None else None
        rebuilt.append(replacement if replacement is not None else text)
    # Sections the destination never had.
    for text in remaining.values():
        if rebuilt and not rebuilt[-1].endswith("\n"):
            rebuilt.append("\n")
        rebuilt.append(text if text.endswith("\n") else text + "\n")

    if not _atomic_write(path, "".join(rebuilt)):
        return 0
    landed = len(sections)
    logger.info(
        "[wine_registry] merged %d registry section(s) into %s",
        landed, Path(prefix).name,
    )
    return landed


def purge_sections(prefix: Path | str, key_prefixes: tuple[str, ...]) -> int:
    """Delete every section under ``key_prefixes``. Returns how many went.

    For sign-out: the token is a registry key, so removing the files alone
    leaves the prefix able to log straight back in.
    """
    path = registry_path(prefix)
    if path is None:
        return 0
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    kept: list[str] = []
    removed = 0
    for key, text in _split_sections(content):
        if _wanted(key, key_prefixes):
            removed += 1
            continue
        kept.append(text)
    if not removed or not _atomic_write(path, "".join(kept)):
        return 0
    logger.info(
        "[wine_registry] purged %d registry section(s) from %s",
        removed, Path(prefix).name,
    )
    return removed
