"""Locate bundled / system CLI binaries via a tiered search.

OP-08d1 | py_modules/unifideck/core/bin/binary_resolver.py

Three-tier lookup, first match wins:

1. **Bundled** — explicit absolute paths in
   ``CLITool.search_paths`` (typically pointing into the
   plugin's ``bin/`` directory).
2. **System PATH** — ``shutil.which(tool.name)``.
3. **User-local** — ``~/.local/bin/<tool.name>``.

The plugin always prefers bundled binaries because their
version is known and tested with the rest of the plugin;
fallbacks exist for dev workflows where the user installed
the tools themselves.

The module exports both the class and a module-level
singleton ``binary_resolver`` for convenience (most callers
don't need per-instance config; for those that do, they
build their own).
"""

import logging
import shutil
import stat
import subprocess
from pathlib import Path

from ..types.domain import CLITool

logger = logging.getLogger(__name__)


def _is_executable(path: str) -> bool:
    """Return whether ``path`` exists, is a regular file, and is owner-executable.

    Three predicates AND'd:

    * ``stat()`` succeeds (file exists + readable);
    * ``S_ISREG`` (regular file — refuses directories,
      symlinks-to-directories, sockets);
    * owner-executable bit set (``S_IXUSR``).

    OSError on stat (broken symlink, permission denied)
    returns False rather than propagating — used in
    fall-through chains where "can't tell" means "skip".

    Args:
        path: filesystem path to test.

    Returns:
        True if executable, False otherwise.
    """
    try:
        st = Path(path).stat()
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    return bool(st.st_mode & stat.S_IXUSR)


class BinaryResolver:
    """Tiered binary-path resolver with optional version check."""

    def __init__(self, config=None) -> None:
        """Initialise with optional config-driven version-check timeout.

        The version timeout defaults to 10 s — generous
        enough for slow CLIs (legendary cold-start can be
        a few seconds) but short enough to catch hangs
        promptly.

        Defensive coercion: a misconfigured value silently
        falls back to the default rather than crashing
        the plugin at boot.

        Args:
            config: optional ``ConfigManager``. Reads
                ``binary_resolver.version_check_timeout_seconds``;
                ``None`` uses the default.
        """
        self._version_timeout = 10
        if config is not None:
            try:
                self._version_timeout = int(
                    config.get("binary_resolver.version_check_timeout_seconds")
                )
            except (TypeError, ValueError):
                pass

    def resolve(self, tool: CLITool) -> str | None:
        """Find the path to ``tool``'s binary, or ``None`` if not located.

        Walks the three tiers in order:

        1. Absolute paths in ``tool.search_paths`` —
           expanded with ``~`` resolution.
        2. ``shutil.which(tool.name)`` for PATH lookup.
        3. ``~/.local/bin/<tool.name>``.

        Each tier checks executability via ``_is_executable``
        so a stale path entry (file removed, permissions
        broken) falls through rather than being returned
        broken. Logs at DEBUG on hit, INFO when nothing
        matched (visible enough to spot config issues).

        Args:
            tool: the ``CLITool`` descriptor.

        Returns:
            Absolute path to the executable, or ``None``
            if not found in any tier.
        """
        for candidate in tool.search_paths:
            expanded = str(Path(candidate).expanduser())
            if Path(expanded).is_absolute() and _is_executable(expanded):
                logger.debug(
                    "[BinaryResolver] %s found in search_paths: %s",
                    tool.name,
                    expanded,
                )
                return expanded
        which = shutil.which(tool.name)
        if which and _is_executable(which):
            logger.debug(
                "[BinaryResolver] %s found in PATH: %s",
                tool.name,
                which,
            )
            return which
        local = Path.home() / ".local" / "bin" / tool.name
        if _is_executable(str(local)):
            logger.debug(
                "[BinaryResolver] %s found in ~/.local/bin",
                tool.name,
            )
            return str(local)
        logger.info(
            "[BinaryResolver] %s not found in any tier",
            tool.name,
        )
        return None

    def check_version(self, tool: CLITool, binary_path: str) -> str | None:
        """Invoke ``<binary_path> <version_flag>`` and return the first line of output.

        Robust against the common quirks:

        * Some CLIs print version to stderr instead of
          stdout (``result.stdout or result.stderr``);
        * Multi-line output (``splitlines()[0]``) takes
          the first line — typically the version, with
          subsequent lines being copyright / dependencies.
        * Three exception classes caught:
          ``TimeoutExpired`` (frozen CLI),
          ``FileNotFoundError`` (race after ``resolve``),
          ``OSError`` (permission flipped, etc.). All log
          at WARN and return ``None``.

        Args:
            tool: ``CLITool`` descriptor with the
                ``version_flag`` to pass.
            binary_path: path returned by ``resolve``.

        Returns:
            Stripped first line of version output, or
            ``None`` on any failure / empty output.
        """
        try:
            result = subprocess.run(
                [binary_path, tool.version_flag],
                capture_output=True,
                text=True,
                timeout=self._version_timeout,
                check=False,
            )
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            OSError,
        ) as e:
            logger.warning(
                "[BinaryResolver] version check failed for %s: %s",
                tool.name,
                e,
            )
            return None
        version = (result.stdout.strip() or result.stderr.strip()).splitlines()
        if version:
            v = version[0].strip()
            logger.debug(
                "[BinaryResolver] %s version: %s",
                tool.name,
                v,
            )
            return v
        return None


binary_resolver = BinaryResolver()
