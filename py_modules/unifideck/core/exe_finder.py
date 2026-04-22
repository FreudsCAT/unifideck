"""core/exe_finder.py — Game executable locator.

# OP-04c | core/exe_finder.py | Depends: OP-05

Walks an install dir (max depth 3), filters known wrappers,
scores candidates by hint match > shallow depth > file size.
Consolidates exe detection logic from Epic/GOG/Amazon/Ubisoft.
"""
from __future__ import annotations

# Known wrapper/launcher executables to skip during scan.
# Crash handlers, redistributable installers, uninstallers,
# and generic helpers that are never the main game binary.
WRAPPER_EXES = {
    # Crash handlers
    "unitycrashhandler64.exe", "unitycrashhandler32.exe",
    "crashreportclient.exe", "crashpad_handler.exe", "bugreport.exe",
    # Redistributable installers
    "ue4prereqsetup_x64.exe", "dxwebsetup.exe",
    "vcredist_x64.exe", "vcredist_x86.exe",
    "dotnetfx35setup.exe", "ndp48-x86-x64-allos-enu.exe",
    "dxsetup.exe",
    # Uninstallers
    "unins000.exe", "unins001.exe", "uninstall.exe",
    # Generic launchers/updaters
    "installer.exe", "setup.exe", "updater.exe",
    "patcher.exe", "launcher.exe",
    # UE4/UE5 specific
    "unrealcefsubprocess.exe",
}


class ExeFinder:
    """Find the main game executable in an install directory.

    Scoring (higher = better):
      +1000 if filename matches a hint (store metadata)
      +(4 - depth) * 100 — prefer shallower paths
      +min(size_mb, 500) — main binary is usually the biggest
    """

    def find(self, install_path: str, hints: list[str] | None = None) -> str | None:
        """Return absolute path to the best .exe candidate, or None.

        Returns None when ``install_path`` is missing, unreadable, or
        contains no scoreable .exe. ``hints`` come from store metadata
        or ``games.map`` and weight strongly in scoring.
        """
        raise NotImplementedError("OP-04c: walk dir, score candidates, return best")

    def _walk_exe_candidates(self, install_path: str):
        """Yield ``(full_path, depth, filename)`` for each scoreable .exe.

        Walks up to depth 3. Skips filenames in ``WRAPPER_EXES``.
        """
        raise NotImplementedError("OP-04c: os.walk with depth limit, yield tuples")

    @staticmethod
    def _score_candidate(
        full_path: str, depth: int, filename: str, hint_lower: set,
    ) -> int:
        """Return heuristic score for one .exe candidate (pure function).
        Silently returns the hint+depth component if ``stat`` fails.
        """
        raise NotImplementedError("OP-04c: implement scoring formula")

    @staticmethod
    def _rank_candidates(
        candidates: list[tuple], install_path: str,
    ) -> str | None:
        """Return highest-scoring candidate, or None. Log best at INFO."""
        raise NotImplementedError("OP-04c: max(candidates, key=score) or None")


# Singleton instance — shared across all stores
exe_finder = ExeFinder()
