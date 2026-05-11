"""legendary.py — Thin async wrapper around ``legendary info``.

# OP-48h | py_modules/unifideck/stores/epic/legendary.py | Depends: OP-07a

Single function used by both :mod:`.exe_resolver` and
:mod:`.updates` to read the per-game manifest from the legendary
CLI without each module duplicating the subprocess + JSON parse.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

_logger = logging.getLogger(__name__)


async def fetch_info(
    cli_path: str,
    game_id: str,
    *,
    timeout: float,
    log_prefix: str = '[epic_legendary]',
) -> dict[str, Any] | None:
    """Fetch info."""
    if not cli_path or not game_id:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path, 'info', game_id, '--json',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        _logger.warning('%s spawn legendary failed: %s', log_prefix, e)
        return None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        _logger.warning('%s legendary info %s timed out', log_prefix, game_id)
        return None
    if proc.returncode != 0:
        _logger.debug(
            '%s legendary info %s rc=%s err=%s',
            log_prefix, game_id, proc.returncode,
            stderr.decode('utf-8', errors='replace')[:200],
        )
        return None
    try:
        data = json.loads(stdout.decode('utf-8', errors='replace'))
    except json.JSONDecodeError as e:
        _logger.warning('%s json decode failed: %s', log_prefix, e)
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None
