"""Serialises nile invocations that can rewrite its credential file.

nile keeps its session in ``~/.config/nile/user.json`` and refreshes the
token opportunistically — most subcommands call ``is_logged_in`` first, and
a refresh rewrites the file. That write is not atomic, so two nile processes
overlapping can leave the file damaged.

That is not theoretical. On this device the file ended up 4621 bytes with
valid JSON through byte 4620 and a single trailing ``}`` — the signature of
a short write landing over a longer file. Every subsequent nile call then
died in ``is_logged_in`` before doing any work::

    File "nile/api/authorization.py", line 164, in is_logged_in
    File "nile/utils/config.py", line 93, in get
    json.decoder.JSONDecodeError: Extra data: line 1 column 4621

which surfaces as Amazon "logged out", failed installs, and the auth flow
reporting ``get_url_failed`` — all from one stray byte. It was triggered by
two concurrent ``nile install --info`` size lookups; the same collision is
available to a library sync running while the user signs in.

Only the SHORT metadata/auth commands take this lock. A running install
holds nile for minutes and is already serialised by the download queue, so
including it here would stall every size lookup behind a download.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level: one nile config file per user, so one lock per process.
_NILE_CLI_LOCK = asyncio.Lock()

NILE_USER_FILE = "~/.config/nile/user.json"


def nile_cli_lock() -> asyncio.Lock:
    """The process-wide lock guarding short nile invocations."""
    return _NILE_CLI_LOCK


def quarantine_corrupt_user_file(user_file: str = NILE_USER_FILE) -> bool:
    """Move an unparseable nile ``user.json`` aside. True if we moved one.

    Once that file is damaged, nile cannot repair itself: EVERY subcommand
    routes through ``is_logged_in`` → ``config.get`` → ``json.loads`` and
    dies there. Verified on-device — even ``nile auth --logout``, the
    obvious remedy, exits 1 with the same ``JSONDecodeError`` and leaves the
    file byte-for-byte unchanged. So a CLI-level self-heal is impossible;
    the file has to be cleared by us, from outside nile.

    Renamed rather than deleted, with a timestamp suffix: the credentials
    inside are already unusable, but keeping the artefact means a corruption
    report can still be diagnosed after the user has signed back in.

    Never raises — a failure here just leaves the caller where it was.
    """
    path = Path(user_file).expanduser()
    try:
        if not path.is_file():
            return False
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    except json.JSONDecodeError as e:
        backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.rename(backup)
        except OSError as move_err:
            logger.warning(
                "[nile] user.json is corrupt (%s) but could not be moved "
                "aside: %s", e, move_err,
            )
            return False
        logger.warning(
            "[nile] user.json was unparseable (%s) — moved to %s so nile can "
            "start clean; the user must sign in to Amazon again.",
            e, backup.name,
        )
        return True
    return False
