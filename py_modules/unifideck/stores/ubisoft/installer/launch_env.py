"""launch_env.py — UPC launch environment dataclass + error type.

# OP-56c | py_modules/unifideck/stores/ubisoft/installer/launch_env.py | Depends: (none)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _UpcLaunchEnv:
    """UPC launch env."""

    upc_path: str
    umu_run: str
    python_bin: str
    env: dict[str, str]


class UpcLaunchEnvBuildError(Exception):
    """Raised when the runtime can't be assembled to launch UPC."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code
