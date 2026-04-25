"""core/exe_finder.py — Game executable locator.

# OP-04c | core/exe_finder.py | Depends: OP-05

Walks an install dir (max depth 3), filters known wrappers,
scores candidates by hint match > shallow depth > file size.
Consolidates exe detection logic from Epic/GOG/Amazon/Ubisoft.
"""
from __future__ import annotations

import logging
import os
from typing import Generator

logger = logging.getLogger(__name__)

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
        if not install_path or not os.path.isdir(install_path):
            return None

        hint_lower = {h.lower() for h in (hints or [])}
        candidates: list[tuple[str, int, str, int]] = []

        for full_path, depth, filename in self._walk_exe_candidates(install_path):
            score = self._score_candidate(full_path, depth, filename, hint_lower)
            candidates.append((full_path, depth, filename, score))

        return self._rank_candidates(candidates, install_path)

    def _walk_exe_candidates(self, install_path: str) -> Generator[tuple[str, int, str], None, None]:
        """Yield ``(full_path, depth, filename)`` for each scoreable .exe.

        Walks up to depth 3. Skips filenames in ``WRAPPER_EXES``.
        """
        install_path = os.path.abspath(install_path)
        prefix_len = len(install_path)

        for root, dirs, files in os.walk(install_path):
            # Compute depth from install_path
            rel = root[prefix_len:]
            depth = rel.count(os.sep) if rel else 0

            if depth >= 3:
                dirs.clear()
                continue

            for filename in files:
                if not filename.lower().endswith(".exe"):
                    continue
                if filename.lower() in WRAPPER_EXES:
                    continue
                yield (os.path.join(root, filename), depth, filename)

    @staticmethod
    def _score_candidate(
        full_path: str, depth: int, filename: str, hint_lower: set,
    ) -> int:
        """Return heuristic score for one .exe candidate (pure function).
        Silently returns the hint+depth component if ``stat`` fails.
        """
        score = 0

        # Hint match: +1000
        if filename.lower() in hint_lower:
            score += 1000

        # Depth preference: shallower is better
        score += (4 - depth) * 100

        # Size tiebreaker: bigger binary is usually the game
        try:
            size_mb = os.path.getsize(full_path) / (1024 * 1024)
            score += min(int(size_mb), 500)
        except OSError:
            pass

        return score

    @staticmethod
    def _rank_candidates(
        candidates: list[tuple], install_path: str,
    ) -> str | None:
        """Return highest-scoring candidate, or None. Log best at INFO."""
        if not candidates:
            return None
        best = max(candidates, key=lambda c: c[3])  # index 3 = score
        logger.info(
            "[ExeFinder] Best exe for %s: %s (score=%d)",
            install_path, best[0], best[3],
        )
        return best[0]


# Singleton instance — shared across all stores
exe_finder = ExeFinder()
