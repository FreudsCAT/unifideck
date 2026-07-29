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

# Module-level: one nile config file per user, so one lock per process.
_NILE_CLI_LOCK = asyncio.Lock()


def nile_cli_lock() -> asyncio.Lock:
    """The process-wide lock guarding short nile invocations."""
    return _NILE_CLI_LOCK
