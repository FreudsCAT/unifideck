from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
_STUB_RELATIVE_PATH = "bin/stubs/GalaxyCommunication.exe"
_TARGET_SUBPATH = str(Path("ProgramData") / "GOG.com" / "Galaxy" / "redists" / "GalaxyCommunication.exe")
def _resolve_drive_c(prefix_path: str) -> str | None:
    """Resolve drive c."""
    from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_drive_c
    result = resolve_drive_c(prefix_path)
    return str(result) if result is not None else None
def _atomic_copy_file(src: Path | str, dst: str) -> None:
    """Atomic copy file."""
    target_dir = str(Path(dst).parent)
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".GalaxyCommunication.", suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "wb") as tmp_fh, \
                Path(str(src)).open("rb") as src_fh:
            shutil.copyfileobj(src_fh, tmp_fh)
            tmp_fh.flush()
            os.fsync(tmp_fh.fileno())
        Path(tmp_path).replace(dst)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()
        raise

def install_galaxy_stub(
    prefix_path: str,
    plugin_dir: Path | None = None,
) -> bool:

    """Install galaxy stub."""
    if plugin_dir is None:
        from unifideck.core.paths import resolve_plugin_dir
        plugin_dir = resolve_plugin_dir()
    stub_src = plugin_dir / _STUB_RELATIVE_PATH
    if not stub_src.is_file():
        logger.warning(
            "[galaxy_stub] stub binary missing at %s — GOG games "
            "that check for Galaxy may fail to launch", stub_src,
        )
        return False
    drive_c = _resolve_drive_c(prefix_path)
    if drive_c is None:
        logger.warning(
            "[galaxy_stub] drive_c not found under %s — prefix "
            "not yet initialised", prefix_path,
        )
        return False
    target_file = str(Path(drive_c) / _TARGET_SUBPATH)
    if Path(target_file).exists():
        logger.debug(
            "[galaxy_stub] stub already installed at %s", target_file,
        )
        return True
    try:
        _atomic_copy_file(stub_src, target_file)
    except OSError as err:
        logger.warning(
            "[galaxy_stub] copy %s → %s failed: %s",
            stub_src, target_file, err,
        )
        return False
    logger.info(
        "[galaxy_stub] installed GalaxyCommunication.exe stub at %s",
        target_file,
    )
    return True
