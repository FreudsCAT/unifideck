"""core/bin/binary_resolver.py — Generic CLI tool locator.

# OP-07a | core/bin/binary_resolver.py | Depends: OP-05

3-tier search: explicit ``CLITool.search_paths`` → system PATH
(``shutil.which``) → ``~/.local/bin/<name>``. Only executable
regular files are returned. Replaces per-store ``_find_legendary``,
``_find_nile``, ``_find_gogdl`` duplicates.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

from ..types.domain import CLITool

logger = logging.getLogger(__name__)


def _is_executable(path: str) -> bool:
    """Return True if ``path`` is a regular file with user-exec bit set.
    False on OSError, symlinks to missing targets, directories,
    or anything non-file.
    """
    try:
        return os.path.isfile(path) and os.access(path, os.X_OK)
    except OSError:
        return False


class BinaryResolver:
    """Generic CLI tool locator shared across all stores."""

    def __init__(self, config=None) -> None:
        """Load ``binary_resolver.version_check_timeout_seconds`` from
        ``config`` (default 10). Best-effort on type/value errors.
        """
        self._version_timeout: int = 10
        if config is not None:
            try:
                val = config.get("binary_resolver.version_check_timeout_seconds", 10)
                if isinstance(val, int) and val > 0:
                    self._version_timeout = val
            except Exception:
                pass

    def resolve(self, tool: CLITool) -> str | None:
        """Locate the binary for ``tool`` via the 3-tier search.
        Returns absolute path to an executable, or None if nowhere.
        Logs the tier that matched at DEBUG; logs "not found" at INFO.
        """
        # Tier 1: explicit search paths (relative to plugin dir)
        for search_path in tool.search_paths:
            expanded = os.path.expanduser(search_path)
            if not os.path.isabs(expanded):
                from ..paths import resolve_plugin_dir
                expanded = str(resolve_plugin_dir() / expanded)
            if _is_executable(expanded):
                logger.debug("[BinaryResolver] Found %s via search_paths: %s", tool.name, expanded)
                return expanded

        # Tier 2: system PATH
        system_path = shutil.which(tool.name)
        if system_path and _is_executable(system_path):
            logger.debug("[BinaryResolver] Found %s via system PATH: %s", tool.name, system_path)
            return system_path

        # Tier 3: ~/.local/bin/<name>
        local_path = os.path.expanduser(f"~/.local/bin/{tool.name}")
        if _is_executable(local_path):
            logger.debug("[BinaryResolver] Found %s via ~/.local/bin: %s", tool.name, local_path)
            return local_path

        logger.info(
            "[BinaryResolver] %s not found. Install with: pip install --user %s",
            tool.name, tool.name,
        )
        return None

    def check_version(
        self,
        tool: CLITool,
        binary_path: str,
    ) -> str | None:
        """Run ``<binary> <version_flag>``, return first non-empty
        output line as the version. Bounded by ``self._version_timeout``.
        Returns None on timeout, missing file, or no output.
        """
        try:
            result = subprocess.run(
                [binary_path, tool.version_flag],
                capture_output=True,
                text=True,
                timeout=self._version_timeout,
            )
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
            return None
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None


# Singleton instance — shared across all stores
binary_resolver = BinaryResolver()
