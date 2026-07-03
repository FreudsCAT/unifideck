"""security/secure_io.py — Hardened file I/O for token storage.

Centralises the security-critical filesystem operations used by
every token manager (GOG, Microsoft, and any future store that
persists encrypted credentials). Replaces direct ``open()`` /
``os.open()`` calls with two primitives that defend against the
classes of local-attacker tricks that bare ``open()`` is naive
about:

  - **Symlink redirection** on the target file: an adversary
    with write access to the parent directory could swap the
    token file for a symlink pointing elsewhere, causing the
    token manager to read or overwrite an unintended file.
    ``O_NOFOLLOW`` makes ``open()`` fail with ``ELOOP`` when the
    final path component is a symlink, closing this avenue.
  - **Pre-positioned temp file** during atomic write: the
    classic tmp-then-rename pattern is safe against partial
    writes but unsafe against an attacker who pre-creates the
    ``.tmp`` path as a symlink to a system file. We refuse any
    pre-existing tmp file unless it is a regular file owned by
    the current uid (crash recovery), and even then we unlink
    it via ``os.unlink`` (which does not follow symlinks).
  - **World-writable parent directory**: if ``~/.config`` was
    ever ``chmod -R 777``-ed (it happens — bug reports and
    backup restores), any local process could replace the token
    file freely. We refuse to write into such a directory and
    surface a clear error so the operator can fix the mode.

Threat model addressed
----------------------
A non-privileged local process running as the same uid as
Unifideck (or with write access to the parent dir) can no longer
hijack the token I/O path through symlink games. The primitives
do **not** protect against:

  - A process that already has read access to the token file (it
    can read the ciphertext directly — but cannot decrypt it
    without the machine-id; see ``secure_token_store.py``).
  - An attacker who can chmod the parent dir mid-flight (race).
    Mitigation: keep the time between the parent-dir check and
    the open() call as short as possible (we don't sleep
    between them).
  - Hardware attacks (memory dumps, cold boot, etc.) — out of
    scope for any userspace process.

Both functions are synchronous and intended to be invoked from
``asyncio.to_thread()`` by token managers, preserving the
existing non-blocking pattern.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

# Default permissions applied to files we create. 0o600 = owner
# read/write only. Same value enforced by the SecurityService
# auto-repair handler so the two layers agree on the canonical
# mode.
_FILE_MODE = 0o600

# Default permissions for parent directories we have to create.
# 0o700 = owner-only access. Stricter than the typical 0o755
# because token directories should never be readable by other
# uids on the same device.
_DIR_MODE = 0o700

# stat.S_IWOTH bit — "world writable" flag. If a parent dir has
# this bit set, any local process can replace the token file at
# leisure, which defeats the entire encryption layer.
_WORLD_WRITABLE_BIT = stat.S_IWOTH


class SecureIOError(OSError):
    """Raised when a hardened I/O primitive refuses an operation.

    Subclasses ``OSError`` so existing ``except OSError`` blocks
    in token managers still catch us, but a separate type lets
    callers distinguish "filesystem said no" from "we said no
    because of a security policy" when they want to.
    """


# ─── Read primitive ──────────────────────────────────────────────

def secure_read_bytes(path: str | os.PathLike[str]) -> bytes:
    """Read file contents refusing to follow a symlinked target.

    Opens the file with ``O_RDONLY | O_NOFOLLOW``. If the final
    path component is a symlink (regardless of where it points),
    the open fails with ``ELOOP`` and we raise ``SecureIOError``.

    Args:
        path: Absolute or expanded path to read. Caller is
            responsible for tilde-expansion — we do not call
            ``expanduser`` here to keep this primitive
            transparent about exactly which path it touched.

    Returns:
        The file contents as raw bytes.

    Raises:
        SecureIOError: when the path is a symlink, when the file
            does not exist, or for any other ``OSError`` during
            read. The original error's strerror is preserved in
            the message but the ``errno`` is not, on purpose:
            callers should treat any failure here as "treat the
            token file as missing" rather than branching on
            errno values that vary across platforms.
    """
    path_str = os.fspath(path)
    try:
        # O_NOFOLLOW: if the final component is a symlink, open
        # raises OSError with errno=ELOOP. This is the entire
        # point of this function. O_CLOEXEC: belt and braces —
        # closes the fd on exec(), which we never do here but
        # costs nothing to set.
        fd = os.open(path_str, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as e:
        raise SecureIOError(
            f"refused to read {path_str}: {e.strerror or e}",
        ) from e
    try:
        with os.fdopen(fd, "rb") as f:
            return f.read()
    except OSError as e:
        # ``os.fdopen`` takes ownership of fd and closes it on
        # exit, including on exception. We don't need to close
        # fd manually here.
        raise SecureIOError(
            f"failed to read {path_str}: {e.strerror or e}",
        ) from e


# ─── Write primitive ─────────────────────────────────────────────

def secure_write_atomic(
    path: str | os.PathLike[str],
    blob: bytes,
    *,
    file_mode: int = _FILE_MODE,
    dir_mode: int = _DIR_MODE,
) -> None:
    """Atomically write ``blob`` to ``path`` with hardened checks.

    Implements the classic ``tmp + os.replace`` atomic-write
    pattern with three additional defences:

      1. Refuse to write if the parent directory is world-
         writable (``S_IWOTH`` bit set) — the encryption layer
         is moot if any local process can replace the file.
      2. ``O_NOFOLLOW | O_EXCL`` on the temp file — refuses to
         overwrite a pre-positioned symlink or regular file.
      3. ``os.replace`` of the temp into place — atomic, and
         since the source is owned by us with mode ``file_mode``,
         the result inherits those properties.

    Crash recovery: if a previous run died between ``O_EXCL``
    open and ``os.replace``, the temp file may persist. We
    handle this by lstat-ing the temp path: if it exists and is
    a regular file owned by our uid, we ``unlink`` it (which
    does not follow symlinks) and proceed. Anything else (a
    symlink, a fifo, a dir, a file owned by someone else) is
    refused outright with ``SecureIOError``.

    Args:
        path: Absolute path to write. Caller must expand ``~``.
        blob: Bytes to write.
        file_mode: Permissions for the created file
            (default ``0o600``). Set at ``open()`` time via the
            ``mode`` argument of ``os.open``, so there is no
            chmod race window between create and the actual
            write.
        dir_mode: Permissions used if we have to create the
            parent directory (default ``0o700``). Existing
            parent dirs are not chmod-ed — we only check the
            world-writable bit and refuse if present.

    Raises:
        SecureIOError: parent dir refusal, pre-existing temp
            file we won't unlink, symlinked target, or any
            ``OSError`` during the actual I/O. The temp file is
            always removed on failure (best-effort) so a
            subsequent retry is not blocked by leftover state.
    """
    path_str = os.fspath(path)
    parent = str(Path(path_str).parent)
    if parent:
        _ensure_parent_dir(parent, dir_mode)
    tmp = path_str + ".tmp"
    _clear_stale_tmp(tmp)
    _write_tmp_then_replace(tmp, path_str, blob, file_mode)


def _ensure_parent_dir(parent: str, dir_mode: int) -> None:
    """Create the parent dir if missing; refuse if world-writable.

    Extracted so ``secure_write_atomic`` stays small. Uses
    ``Path.mkdir`` rather than ``os.makedirs`` because we want
    the new dir to receive ``dir_mode`` exactly (mkdir applies
    it on creation; makedirs default is umask-influenced).
    """
    try:
        Path(parent).mkdir(parents=True, exist_ok=True, mode=dir_mode)
    except OSError as e:
        raise SecureIOError(
            f"cannot create parent dir {parent}: {e.strerror or e}",
        ) from e
    try:
        st = Path(parent).stat()
    except OSError as e:
        raise SecureIOError(
            f"cannot stat parent dir {parent}: {e.strerror or e}",
        ) from e
    if st.st_mode & _WORLD_WRITABLE_BIT:
        raise SecureIOError(
            f"refusing to write into world-writable directory "
            f"{parent} (mode {oct(st.st_mode & 0o7777)}); "
            f"chmod o-w to fix",
        )


def _clear_stale_tmp(tmp: str) -> None:
    """Remove a leftover ``.tmp`` file from a previous crash.

    Safe-removal policy: only unlink if the path is a regular
    file owned by the current uid. Anything else (symlink,
    socket, dir, fifo, file owned by a different uid) is a
    refusal — we won't try to clean up something an attacker
    might have planted.

    Uses ``os.lstat`` (does not follow symlinks) so we get the
    truth about the temp path regardless of where it points.
    """
    try:
        st = os.lstat(tmp)
    except FileNotFoundError:
        # Normal case: no leftover. Proceed.
        return
    except OSError as e:
        raise SecureIOError(
            f"cannot lstat temp path {tmp}: {e.strerror or e}",
        ) from e
    if not stat.S_ISREG(st.st_mode):
        raise SecureIOError(
            f"refusing to overwrite non-regular temp path {tmp} "
            f"(mode {oct(st.st_mode & 0o7777)}); "
            f"manual cleanup required",
        )
    if st.st_uid != os.getuid():
        raise SecureIOError(
            f"refusing to overwrite temp path {tmp} owned by "
            f"uid {st.st_uid}; manual cleanup required",
        )
    try:
        Path(tmp).unlink()
    except OSError as e:
        raise SecureIOError(
            f"cannot unlink stale temp {tmp}: {e.strerror or e}",
        ) from e
    logger.info("[secure_io] cleared stale temp file: %s", tmp)


def _write_tmp_then_replace(
    tmp: str, path: str, blob: bytes, file_mode: int,
) -> None:
    """Open temp with O_EXCL|O_NOFOLLOW, write, then atomic replace.

    Three-step pattern:

      1. ``os.open(tmp, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW,
         file_mode)`` — refuses to overwrite anything (O_EXCL
         after our pre-cleanup) and refuses to follow symlinks
         on the temp path itself (defence in depth, since
         ``_clear_stale_tmp`` already checked). Mode is set at
         creation, no chmod race.
      2. ``os.fdopen(fd, "wb").write(blob)`` — writes and
         closes the fd (fdopen takes ownership).
      3. ``os.replace(tmp, path)`` — atomic rename. After this
         call any reader sees either the old file or the new
         one, never a partial write.

    On any failure the temp file is removed best-effort so
    later retries succeed.
    """
    try:
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | os.O_NOFOLLOW | os.O_CLOEXEC,
            file_mode,
        )
    except OSError as e:
        raise SecureIOError(
            f"cannot create temp {tmp}: {e.strerror or e}",
        ) from e
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        Path(tmp).replace(path)
    except OSError as e:
        _best_effort_unlink(tmp)
        raise SecureIOError(
            f"failed to commit {path}: {e.strerror or e}",
        ) from e


def _best_effort_unlink(path: str) -> None:
    """Remove ``path`` swallowing any error.

    Used in cleanup paths where surfacing the error would mask
    the original failure that triggered the cleanup.
    """
    try:
        Path(path).unlink()
    except OSError as e:
        logger.debug(
            "[secure_io] cleanup unlink failed for %s: %s",
            path, e,
        )
