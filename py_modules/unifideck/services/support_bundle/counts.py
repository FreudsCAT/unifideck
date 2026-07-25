"""support_bundle/counts.py — Count things in audited files.

Small read-only helpers used by the sanity checks. Split out of
``checks.py`` to keep that module under the file-size cap, and because
"count the entries in this file" is a genuinely separate concern from
"decide whether that count is a problem".

Every function returns ``None`` rather than raising when the file is
absent or unparseable: a check that cannot get its input reports ``N/A``,
which is different from reporting a failure.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .spec import PathRecord

logger = logging.getLogger(__name__)

# Each shortcut entry in Steam's binary VDF carries exactly one
# ``appid`` key, so counting the type-tagged key is a reliable entry
# count without a full binary-VDF parse (and without depending on the
# vendored vdf library being importable).
_VDF_APPID_KEY = b"\x02appid\x00"


def _path_of(record: PathRecord | None) -> Path | None:
    """Resolve an audit record to a readable file path."""
    if record is None or not record.expected_path:
        return None
    path = Path(record.expected_path)
    return path if path.is_file() else None


def vdf_entries(record: PathRecord | None) -> int | None:
    """Number of shortcut entries in a binary ``shortcuts.vdf``."""
    path = _path_of(record)
    if path is None:
        return None
    try:
        return path.read_bytes().count(_VDF_APPID_KEY)
    except OSError:
        return None


def load_json(record: PathRecord | None) -> Any:
    """Parse an audited JSON file, ``None`` on any failure."""
    path = _path_of(record)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def json_entries(record: PathRecord | None) -> int | None:
    """Entry count of a JSON object or array."""
    parsed = load_json(record)
    if isinstance(parsed, (dict, list)):
        return len(parsed)
    return None


def text_lines(record: PathRecord | None) -> int | None:
    """Count content lines, ignoring blanks and ``#`` comments.

    ``games.map`` carries a two-line comment header describing its own
    format, which would otherwise inflate every count by two.
    """
    path = _path_of(record)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return sum(
        1 for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def shortcut_counts(by_key: dict[str, PathRecord]) -> dict[str, int | None]:
    """Count entries in each place shortcut state is recorded.

    These three numbers are **not** expected to be equal, and treating
    them as if they were produced a failing verdict on a perfectly
    healthy device:

    * ``shortcuts_vdf`` counts the shortcuts Steam currently has;
    * ``registry`` is keyed by ``store:game_id`` and records every game
      the plugin has ever created a shortcut for, so it legitimately
      exceeds the live count after hiding, dedup or removal;
    * ``games_map`` is the launcher's manifest and is smaller again.

    They are surfaced together because the *ratio* is informative to a
    human reading the bundle. Only one relationship is unambiguous, and
    that is the one the check asserts on — see
    :func:`checks._check_triangulation`.
    """
    return {
        "shortcuts_vdf": vdf_entries(by_key.get("shortcuts_vdf")),
        "registry": json_entries(by_key.get("shortcuts_registry")),
        "games_map": text_lines(by_key.get("games_map")),
    }
