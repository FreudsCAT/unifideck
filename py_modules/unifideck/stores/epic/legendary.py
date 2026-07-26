"""Thin wrapper for the ``legendary`` CLI — info-fetch helper.

OP-48h | py_modules/unifideck/stores/epic/legendary.py

A single module-level function (no class) that wraps the
``legendary info <game_id>`` subprocess call and returns the parsed
JSON output. Used by ``install.py``, ``library.py``, and
``updates.py`` whenever they need game metadata.

``fetch_info(game_id, legendary_bin)`` :

* spawns ``legendary info --json <game_id>``;
* parses stdout as JSON;
* returns the parsed dict (or None on failure);
* swallows subprocess errors and logs them — the caller decides
  how to react to a missing info response.

Kept as a one-function module because the surface is trivial and
extracting it elsewhere would just spread the legendary-CLI
coupling across more files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

from unifideck.core.binaries import clean_cli_env

_logger = logging.getLogger(__name__)


async def fetch_info(cli_path: str, game_id: str, *, timeout: float, log_prefix: str = "[epic_legendary]") -> dict[str, Any] | None:  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
    """Fetch info."""
    if not cli_path:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path,
            "info",
            game_id,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=clean_cli_env(),
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except (TimeoutError, OSError) as e:
        _logger.warning("%s legendary info failed: %s", log_prefix, e)
        return None
    if proc.returncode != 0:
        return None
    try:
        return cast("dict[str, Any] | None", json.loads(stdout.decode(errors="ignore")))
    except json.JSONDecodeError as e:
        _logger.warning(
            "%s JSON parse error: %s",
            log_prefix,
            e,
        )
        return None
