from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
PathLike = str | Path
def normalize_prefix_root(prefix_path: PathLike) -> Path:
    """Normalize prefix root."""
    p = Path(prefix_path).resolve() if isinstance(prefix_path, str) \
        else prefix_path.resolve()
    while p.name == "pfx":
        p = p.parent
    return p
def resolve_registry_prefix(prefix_root: PathLike) -> Path:
    """Resolve registry prefix."""
    root = Path(prefix_root) if isinstance(prefix_root, str) \
        else prefix_root
    direct = root / "user.reg"
    pfx = root / "pfx"
    pfx_reg = pfx / "user.reg"
    if direct.exists():
        return root
    if pfx_reg.exists():
        return pfx
    if pfx.is_dir():
        return pfx
    return root
def resolve_drive_c(prefix_root: PathLike) -> Path | None:
    """Resolve drive c."""
    root = Path(prefix_root) if isinstance(prefix_root, str) \
        else prefix_root
    modern = root / "pfx" / "drive_c"
    if modern.is_dir():
        return modern
    legacy = root / "drive_c"
    if legacy.is_dir():
        return legacy
    return None
