"""support_bundle/probe_plugin_logs.py — Other Decky plugins' own logs.

``probe_conflicts.plugins_block`` already lists which plugins are installed
and at what version. That is enough to *suspect* interference and never
enough to prove it, which left a whole class of report unanswerable:

    "CSS Loader themes didn't work"

CSS Loader injects from its own Python backend over CDP and records the
outcome in ``~/homebrew/logs/SDH-CssLoader/`` — one line there
(``Committing css transaction on Steam Big Picture Mode +22 -0`` versus
``Cannot connect to host 127.0.0.1:8080``) decides whether the problem is
ours at all. The bundle collected plugin names and versions but not a single
byte of any sibling log, so the answer was always "please send us another
file". Generalises: the next such report will name a different plugin.

Scope is deliberately narrow, because these are **other people's logs**:

* the **newest** log file per plugin only — one per plugin, so a chatty
  plugin cannot crowd out a quiet one (a single ``*/*.log`` glob with a
  newest-N cap would have done exactly that);
* the **tail** of each, capped;
* our own plugin is skipped — ``decky_session_logs`` already collects it in
  full, and duplicating it would spend budget on bytes we already have;
* everything runs through the collector's normal ``text`` scrub profile, the
  same redaction any log of ours gets.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Per-plugin tail cap. Whatever broke is at the end of the file, and 14
#: plugins is a normal count — small enough that the total stays well inside
#: the archive budget even on a heavily-modded device.
CAP_PER_PLUGIN = 96 * 1024

#: Belt-and-braces bound on the whole artifact, independent of plugin count.
CAP_TOTAL = 2 * 1024 * 1024

#: Our own logs are collected in full by the ``decky_session_logs`` row.
_OWN_PLUGIN = "Unifideck"

_HEADER = "=" * 72


def logs_root() -> Path:
    """Decky's per-plugin log directory (parent of every plugin's own)."""
    return Path("~/homebrew/logs").expanduser()


def _newest_log(plugin_dir: Path) -> Path | None:
    """The most recently modified ``*.log`` in *plugin_dir*, or None."""
    try:
        logs = [p for p in plugin_dir.glob("*.log") if p.is_file()]
    except OSError:
        return None
    if not logs:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime if p.exists() else 0)


def _tail(path: Path, limit: int) -> str:
    """Last *limit* bytes of *path* as text, newline-aligned where possible.

    Delegates to the collector's own reader rather than repeating its
    tail-and-align logic: that one already carries the truncation banner
    format every other artifact uses, and the fix for a window containing no
    newline at all. Imported lazily — ``collect`` imports this module.
    """
    from .collect import _read_capped

    try:
        data, _size, _truncated = _read_capped(path, limit, tail=True)
    except OSError as err:
        return f"<unreadable: {err}>"
    return data.decode("utf-8", errors="replace")


def _plugin_dirs() -> list[Path]:
    """Every other plugin's log directory, name-sorted."""
    root = logs_root()
    if not root.is_dir():
        return []
    try:
        return sorted(
            entry for entry in root.iterdir()
            if entry.is_dir() and entry.name != _OWN_PLUGIN
        )
    except OSError:
        logger.debug("[support_bundle] cannot list %s", root, exc_info=True)
        return []


def render_sibling_logs() -> tuple[str, list[dict[str, Any]]]:
    """``(artifact_text, summary_rows)`` for the other plugins' newest logs.

    The summary goes into ``environment.json`` so a reader can see *which*
    plugins contributed — and which had no log at all — without opening the
    artifact.
    """
    sections = [
        "OTHER DECKY PLUGINS' LOGS",
        "=========================",
        "",
        (
            "The newest log file from each other installed plugin, tail-capped "
            f"at {CAP_PER_PLUGIN // 1024} KiB. Unifideck's own logs are under "
            "decky/ in full. Use these to tell OUR bug from a neighbour's."
        ),
        "",
    ]
    summary: list[dict[str, Any]] = []
    used = 0
    for plugin_dir in _plugin_dirs():
        newest = _newest_log(plugin_dir)
        if newest is None:
            summary.append({"plugin": plugin_dir.name, "log": None})
            continue
        if used >= CAP_TOTAL:
            summary.append({
                "plugin": plugin_dir.name, "log": newest.name,
                "collected": False, "reason": "artifact byte cap reached",
            })
            continue
        body = _tail(newest, min(CAP_PER_PLUGIN, CAP_TOTAL - used))
        used += len(body)
        sections += [_HEADER, f"{plugin_dir.name} — {newest.name}", _HEADER, body, ""]
        summary.append({
            "plugin": plugin_dir.name, "log": newest.name,
            "collected": True, "bytes": len(body),
        })
    if not summary:
        sections.append("(no other plugin log directories found)")
    return "\n".join(sections), summary


def plugin_logs_block() -> dict[str, Any]:
    """Summary block for ``environment.json`` — stat only, no file bodies.

    Deliberately does not call :func:`render_sibling_logs`: the environment
    report is built before collection, and sharing that call would mean
    reading every sibling log twice.
    """
    root = logs_root()
    rows: list[dict[str, Any]] = []
    for plugin_dir in _plugin_dirs():
        newest = _newest_log(plugin_dir)
        if newest is None:
            rows.append({"plugin": plugin_dir.name, "log": None})
            continue
        try:
            size = newest.stat().st_size
        except OSError:
            size = None
        rows.append({
            "plugin": plugin_dir.name, "log": newest.name, "bytes": size,
        })
    return {
        "root": str(root),
        "root_exists": root.is_dir(),
        "artifact": "plugins/other-plugin-logs.txt",
        "cap_per_plugin_bytes": CAP_PER_PLUGIN,
        "plugins": rows,
    }
