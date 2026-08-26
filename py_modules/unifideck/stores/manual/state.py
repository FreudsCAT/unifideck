"""Persistent state for the manual store.

py_modules/unifideck/stores/manual/state.py

One JSON file (default ``~/.local/share/unifideck/manual_games.json``)
is the whole store: the manual store has no vendor CLI and no remote
account, so "the library" is exactly the list of games the user added
through the Manual Install flow. Each record moves through two states:

* ``installing`` — the installer .exe is known, the game .exe is not.
  ``get_library`` maps this to a Game whose exe IS the installer, so
  pressing Play (or the automatic RunGame after adding) re-runs the
  installer until the user picks the real executable.
* ``ready`` — the user selected the game's executable; Play launches it.

All functions here are synchronous — callers (the store / RPC mixin)
wrap them in ``asyncio.to_thread``. Writes are atomic (tmp +
``os.replace``) so a crash never truncates the library.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STATUS_INSTALLING = "installing"
STATUS_READY = "ready"


@dataclass
class ManualGameRecord:
    """One manually installed game.

    Attributes:
        game_id: stable store-local id (slug + short hash of the
            installer path) — also names the Proton prefix directory.
        title: user-provided display title.
        installer_path: absolute path of the setup .exe the user picked.
        install_path: the game's directory under the manual install
            root (``~/Games/Manual/<game_id>`` by default). Exposed to
            the installer as Wine drive ``D:`` so the files land
            outside the prefix.
        exe_path: the game's executable once chosen; empty while the
            record is still ``installing``.
        status: ``installing`` or ``ready``.
        added_at: epoch seconds when the record was created.
    """

    game_id: str
    title: str
    installer_path: str
    install_path: str
    exe_path: str = ""
    status: str = STATUS_INSTALLING
    added_at: float = field(default_factory=time.time)


def load_records(path: Path) -> dict[str, ManualGameRecord]:
    """Read the state file into ``{game_id: record}``.

    Any malformed row is dropped with a warning rather than failing the
    whole load — one corrupt entry must not hide the rest of the
    library from sync.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("[ManualState] unreadable state file %s: %s", path, e)
        return {}
    rows = raw.get("games") if isinstance(raw, dict) else None
    records: dict[str, ManualGameRecord] = {}
    for row in rows if isinstance(rows, list) else []:
        record = _record_from_row(row)
        if record is not None:
            records[record.game_id] = record
    return records


def _record_from_row(row: object) -> ManualGameRecord | None:
    """Build one record from a raw JSON row, or ``None`` when invalid."""
    if not isinstance(row, dict):
        return None
    game_id = row.get("game_id")
    title = row.get("title")
    if not isinstance(game_id, str) or not game_id or not isinstance(title, str):
        logger.warning("[ManualState] dropping malformed row: %r", row)
        return None
    return ManualGameRecord(
        game_id=game_id,
        title=title,
        installer_path=str(row.get("installer_path") or ""),
        install_path=str(row.get("install_path") or ""),
        exe_path=str(row.get("exe_path") or ""),
        status=str(row.get("status") or STATUS_INSTALLING),
        added_at=float(row.get("added_at") or 0.0),
    )


def save_records(path: Path, records: dict[str, ManualGameRecord]) -> None:
    """Atomically persist ``records`` to ``path`` (tmp + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "games": [asdict(r) for r in records.values()],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
