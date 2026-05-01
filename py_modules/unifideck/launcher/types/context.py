"""launcher/types/context.py — Immutable launch request context."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LaunchContext:
    """Immutable description of a single launch request.
    Built once by the dispatcher from argv + games.map + env.
    Passed by value to every downstream module. Never mutated.
    """
    store: str
    game_id: str
    exe_path: Path | str
    work_dir: Path | str
    plugin_dir: Path | str
    raw_options: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)
    is_launch_action: bool = True
    auth_store: str | None = None
    bypass_circuit_breaker: bool = False
    steam_app_id: str | None = None
    
    # Extra fields used by some logic
    game: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Any] = field(default_factory=dict)

    @property
    def is_xcloud(self) -> bool:
        """True when this request is an xCloud streaming session."""
        return str(self.exe_path) == "xcloud"

    @property
    def is_windows_game(self) -> bool:
        """True when the exe should route through Proton/UMU."""
        if self.is_xcloud:
            return False
        if self.store == "ubisoft":
            return True
        exe_str = str(self.exe_path).lower()
        return any(exe_str.endswith(ext) for ext in (".exe", ".cmd", ".bat"))

    @property
    def is_native_linux(self) -> bool:
        """True when the exe is a native Linux binary."""
        return not (self.is_xcloud or self.is_windows_game)

    @property
    def game_key(self) -> str:
        """Return the ``store:game_id`` key used in games.map."""
        return f"{self.store}:{self.game_id}"


@dataclass
class RuntimeState:
    """Mutable companion to LaunchContext.
    Collects everything the launcher **derives**.
    """
    proton_path: Path | None = None
    proton_tool_id: str | None = None
    prefix_path: Path | None = None
    umu_store_code: str | None = None
    umu_id: str | None = None
    umu_wrapper: Path | None = None
    python_bin: Path | None = None
    wrappers: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    lsfg_requested: bool = False
    game_exit_code: int | None = None
    terminated_by_signal: bool = False
    
    # Compat field used by LauncherService
    rc: int = 1
    started_at: float = 0.0
