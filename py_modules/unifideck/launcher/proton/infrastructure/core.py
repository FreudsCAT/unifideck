"""launcher/proton/infrastructure/core.py — Shared Proton/UMU launch setup."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...types.context import LaunchContext, RuntimeState

logger = logging.getLogger(__name__)

STORE_TO_UMU = {
    "epic": "egs",
    "gog": "gog",
    "amazon": "amazon",
    "ubisoft": "ubisoft",
    "microsoft": "microsoft",
}


@dataclass(frozen=True)
class ProtonLaunchPlan:
    """Everything store handlers need to spawn umu-run."""
    context: LaunchContext
    state: RuntimeState
    python_bin: Path
    umu_wrapper: Path
    prefix_path: Path
    env: dict[str, str]
    on_process_start: Callable[[Any], None] | None = None

    def get_cmd(self) -> list[str]:
        """Return the base command list (umu-run + python)."""
        return [str(self.python_bin), str(self.umu_wrapper)]

    def get_env(self) -> dict[str, str]:
        """Return the env for the subprocess."""
        return self.env

    def get_cwd(self) -> str:
        """Return the working directory."""
        return self.context.game.get("work_dir", "/")


async def proton_prepare(
    ctx: LaunchContext,
    state: RuntimeState,
) -> ProtonLaunchPlan:
    """Prepare a Proton launch plan by resolving paths and environment."""
    # Resolve prefix
    user_home = Path(os.path.expanduser("~"))
    prefix_root = user_home / ".local/share/unifideck/prefixes"
    game_id = ctx.game.get("game_id", "unknown")
    prefix_path = prefix_root / game_id
    
    if not prefix_path.exists():
        prefix_path.mkdir(parents=True, exist_ok=True)
        
    # Setup environment
    env = os.environ.copy()
    env["WINEPREFIX"] = str(prefix_path)
    env["UMU_STORE"] = STORE_TO_UMU.get(ctx.game.get("store", ""), "generic")
    env["UMU_ID"] = ctx.game.get("game_id", "")
    
    # Paths (simplified discovery)
    python_bin = Path("/usr/bin/python3")
    umu_wrapper = Path("/usr/bin/umu-run") # In real case, discovered via BinaryResolver
    
    return ProtonLaunchPlan(
        context=ctx,
        state=state,
        python_bin=python_bin,
        umu_wrapper=umu_wrapper,
        prefix_path=prefix_path,
        env=env
    )
