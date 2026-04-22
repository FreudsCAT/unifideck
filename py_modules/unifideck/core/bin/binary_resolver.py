"""core/bin/binary_resolver.py — Generic CLI tool locator.

# OP-07a | core/bin/binary_resolver.py | Depends: OP-05

3-tier search: explicit ``CLITool.search_paths`` → system PATH
(``shutil.which``) → ``~/.local/bin/<name>``. Only executable
regular files are returned. Replaces per-store ``_find_legendary``,
``_find_nile``, ``_find_gogdl`` duplicates.
"""
from __future__ import annotations

from ..types.domain import CLITool


def _is_executable(path: str) -> bool:
    """Return True if ``path`` is a regular file with user-exec bit set.
    False on OSError, symlinks to missing targets, directories,
    or anything non-file.
    """
    raise NotImplementedError("OP-07a: implement using os.path.isfile + os.access")


class BinaryResolver:
    """Generic CLI tool locator shared across all stores."""

    def __init__(self, config=None) -> None:
        """Load ``binary_resolver.version_check_timeout_seconds`` from
        ``config`` (default 10). Best-effort on type/value errors.
        """
        raise NotImplementedError("OP-07a: load version_check_timeout from config")

    def resolve(self, tool: CLITool) -> str | None:
        """Locate the binary for ``tool`` via the 3-tier search.
        Returns absolute path to an executable, or None if nowhere.
        Logs the tier that matched at DEBUG; logs "not found" at INFO.
        """
        raise NotImplementedError("OP-07a: implement 3-tier search: search_paths → PATH → ~/.local/bin")

    def check_version(
        self,
        tool: CLITool,
        binary_path: str,
    ) -> str | None:
        """Run ``<binary> <version_flag>``, return first non-empty
        output line as the version. Bounded by ``self._version_timeout``.
        Returns None on timeout, missing file, or no output.
        """
        raise NotImplementedError("OP-07a: implement subprocess version check with timeout")


# Singleton instance — shared across all stores
binary_resolver = BinaryResolver()
