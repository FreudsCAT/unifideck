"""core/bin/binary_resolver.py — Generic CLI tool locator.

Moved from core/ to the new core/bin/
subpackage, grouped with binary_signatures.py (trust) and
cli_timeouts.py (run). The three form the complete lifecycle
of an external CLI tool: find it, verify it, invoke it safely.
Clean break: no shim in core/.

Replaces duplicated _find_legendary / _find_nile / gogdl resolution
in every store connector with a single 3-tier search strategy.
Strategy:
1. Explicit search paths declared in CLITool.search_paths (e.g. bundled
 binaries in `bin/`)
2. System PATH via shutil.which()
3. User-local binaries in ~/.local/bin/<name>
Only executable regular files are returned. Non-executable matches are
skipped so a read-only script file never shadows a real binary later
in PATH.
Reference: Technical Document v1.0 — Section 3.4.2 (BinaryResolver +
ExeFinder), Figure 12.
"""
import contextlib
import logging
import shutil
import stat
import subprocess
from pathlib import Path

from unifideck.core.types.domain import CLITool

logger = logging.getLogger(__name__)


def _is_executable(path: str) -> bool:
    """Return True if path is a regular file with the user-execute bit set."""
    try:
        st = Path(path).stat()
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    return bool(st.st_mode & stat.S_IXUSR)


class BinaryResolver:
    """Generic CLI tool locator shared across all stores.

    Usage::

        resolver = BinaryResolver(config=cfg)
        path = resolver.resolve(
            CLITool("legendary", ["bin/legendary"]),
        )
        if path:
            version = resolver.check_version(tool, path)
    """

    def __init__(self, config=None) -> None:
        # The version-check timeout is the only tunable knob;
        # loaded once at init so we don't pay the config lookup
        # on every resolve() call.
        self._version_timeout = 10
        if config is not None:
            with contextlib.suppress((TypeError, ValueError)):
                self._version_timeout = int(config.get(
                    "binary_resolver.version_check_timeout_seconds"))

    def resolve(self, tool: CLITool) -> str | None:
        """Locate the binary for a CLI tool.

        Args:
          tool: Descriptor with name, search_paths, etc.

        Returns:
          Absolute path to an executable binary, or None if
          not found.

        """
        # Tier 1 — explicit search paths (bundled or hardcoded)
        for candidate in tool.search_paths:
            expanded = str(Path(candidate).expanduser())
            if (
                Path(expanded).is_absolute()
                and _is_executable(expanded)
            ):
                logger.debug(
                    "[BinaryResolver] %s found in search_paths: "
                    "%s",
                    tool.name, expanded,
                )
                return expanded

        # Tier 2 — system PATH
        which = shutil.which(tool.name)
        if which and _is_executable(which):
            logger.debug(
                "[BinaryResolver] %s found in PATH: %s",
                tool.name, which,
            )
            return which

        # Tier 3 — user-local bin
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

    def check_version(
        self, tool: CLITool, binary_path: str,
    ) -> str | None:
        """Run ``<binary> <version_flag>`` and return the first
        non-empty line of output.

        Args:
          tool: CLITool with version_flag (e.g. '--version').
          binary_path: Absolute path to the binary.

        Returns:
          Version string or None if version check fails.

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
                "[BinaryResolver] version check failed for "
                "%s: %s",
                tool.name, e,
            )
            return None
        version = (
            result.stdout.strip() or result.stderr.strip()
        ).splitlines()
        if version:
            v = version[0].strip()
            logger.debug(
                "[BinaryResolver] %s version: %s", tool.name, v,
            )
            return v
        return None


# Singleton instance — shared across all stores
binary_resolver = BinaryResolver()
