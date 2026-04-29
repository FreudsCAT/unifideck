"""core/exe_finder.py — Game executable locator.
Searches install directories and Wine prefixes for the main .exe file.
Replaces duplicated exe detection logic across Epic, GOG, Amazon, and
Ubisoft stores.
Strategy:
1. Walk the install directory tree (max depth 3)
2. Filter out known wrappers/launchers/redistributables
3. Score candidates by: hint match > shallow depth > larger file size
4. Return the highest-scoring candidate
Reference: Technical Document v1.0 — Section 3.4.2.
"""
import logging
import os
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)
# Known wrapper/launcher executables to skip.
# These are common redistributable installers, crash handlers,
# and Unity/Unreal helper binaries that are NOT the main game.
WRAPPER_EXES = {
 # Crash handlers
 "unitycrashhandler64.exe",
 "unitycrashhandler32.exe",
 "crashreportclient.exe",
 "crashpad_handler.exe",
 "bugreport.exe",
 # Redistributable installers
 "ue4prereqsetup_x64.exe",
 "dxwebsetup.exe",
 "vcredist_x64.exe",
 "vcredist_x86.exe",
 "dotnetfx35setup.exe",
 "ndp48-x86-x64-allos-enu.exe",
 "dxsetup.exe",
 # Uninstallers
 "unins000.exe",
 "unins001.exe",
 "uninstall.exe",
 # Generic launchers/updaters
 "installer.exe",
 "setup.exe",
 "updater.exe",
 "patcher.exe",
 "launcher.exe",
 # UE4/UE5 specific (ue4prereqsetup_x64.exe already listed above)
 "unrealcefsubprocess.exe",
}
class ExeFinder:
    """Find the main game executable in an install directory.
    Scores candidates to pick the best match:
    - +1000 if the filename matches a hint (from store metadata)
    - +(4-depth)*100 for shallower directories (prefer root)
    - +min(size_mb, 500) for larger files (main binaries are usually bigger)
    Usage:
    finder = ExeFinder()
    exe = finder.find("/path/to/game", hints=["Game.exe"]).
    """

    def find(
    self,
    install_path: str,
    hints: list[str] | None = None,
    ) -> str | None:
        """Find the main .exe in install_path.

        Args:
          install_path: Game installation directory.
          hints: Optional list of known exe names to prefer
            (e.g. from store metadata or games.map).

        Returns:
          Absolute path to the best .exe candidate, or None.

        """
        if not install_path or not Path(install_path).is_dir():
            return None

        hint_lower = {h.lower() for h in hints} if hints else set()

        candidates = [
            (
                self._score_candidate(
                    path, depth, filename, hint_lower,
                ),
                path,
            )
            for path, depth, filename in (
                self._walk_exe_candidates(install_path)
            )
        ]

        return self._rank_candidates(candidates, install_path)

    def _walk_exe_candidates(
        self, install_path: str,
    ):
        """Yield (full_path, depth, filename) for every scoreable .exe.

        Side effect: filesystem walk. Depth is capped at 3
        levels to keep the scan bounded on games with deep
        asset hierarchies. Wrapper binaries (unins*, vcredist,
        launcher helpers) are filtered here to keep the scorer
        pure.
        """
        for root, dirs, files in os.walk(install_path):
            rel = os.path.relpath(root, install_path)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > 3:
                dirs.clear()  # stop descending this branch
                continue
            for filename in files:
                lower = filename.lower()
                if not lower.endswith(".exe"):
                    continue
                if lower in WRAPPER_EXES:
                    continue
                yield str(Path(root) / filename), depth, filename

    @staticmethod
    def _score_candidate(
        full_path: str,
        depth: int,
        filename: str,
        hint_lower: set,
    ) -> int:
        """Return a heuristic score for one .exe candidate.

        Pure function: no I/O except ``Path.stat().st_size``
        which is wrapped in try/except OSError. The three
        signals, from strongest to weakest, are hint match
        (+1000), shallow depth (+100 per level), and file size
        in MB (capped at 500).

        The hint match dominates because metadata from a store
        is usually right. Size dominates in the absence of
        hints because the main game binary is typically the
        largest .exe in the install tree (engines, splash
        wrappers, crash handlers are smaller).
        """
        score = 0
        if filename.lower() in hint_lower:
            score += 1000
        score += (4 - depth) * 100
        try:
            size_mb = (
                Path(full_path).stat().st_size // (1024 * 1024)
            )
            score += min(size_mb, 500)
        except OSError:
            # Missing / unreadable file — the walk shouldn't
            # return it, but defend anyway so we never raise
            # from scoring.
            pass
        return score

    @staticmethod
    def _rank_candidates(
    candidates: list[tuple],
    install_path: str,
    ) -> str | None:
        """Pick the highest-scoring candidate, or None if empty.

        Pure function. Logging lives here (not in the scorer) because
        the "no candidates" case is a useful diagnostic at DEBUG level
        and we only want one log line per `find()` call.
        """
        if not candidates:
            logger.debug(
                "[ExeFinder] No .exe found in %s", install_path,
            )
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_path = candidates[0]
        logger.info(
            "[ExeFinder] Best candidate (score=%d): %s",
            best_score, best_path,
        )
        return cast("str | None", best_path)


# Singleton instance — shared across all stores
exe_finder = ExeFinder()
