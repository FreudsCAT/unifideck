"""support_bundle/path_audit.py — Where everything should be.

The most useful artifact in the bundle. Walks the entire registry —
including the rows we never ship — and reports, for each one: the path
it resolved to, which resolver rung produced that path, whether it is
actually there, its size, mtime and mode, and whether being absent is
normal.

That last field is what makes the table readable. A list of MISSING
rows with no notion of expectation is noise; ``expect`` turns it into a
signal, because absent Amazon state on a device with no Amazon account
means nothing while an absent ``shortcuts.vdf`` never does.

Credential rows are audited here too, and this is the only place they
are touched at all: :func:`_stat_record` calls ``stat()`` and nothing
else. Their contents are never opened, by this module or any other.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from . import resolve
from .deny import is_denied
from .sources_audit import all_sources
from .spec import BundleContext, PathRecord, SourceSpec

logger = logging.getLogger(__name__)

# Human labels for what happens to a row, keyed by its policy. The
# collector overrides these for rows it actually skipped at runtime.
_ACTION_BY_POLICY = {
    "include": "included",
    "tail": "included_tailed",
    "presence_only": "presence_only",
    "bulk": "excluded_bulk",
    "static": "excluded_static",
    "skip": "excluded_by_policy",
}


def audit_paths(ctx: BundleContext) -> list[PathRecord]:
    """Audit every registry row against the filesystem."""
    records: list[PathRecord] = []
    for spec in all_sources():
        records.extend(_audit_one(spec, ctx))
    return records


def _audit_one(spec: SourceSpec, ctx: BundleContext) -> list[PathRecord]:
    """Audit one registry row, which may expand to several paths."""
    if spec.root == "generated":
        return []
    if spec.root == "paths":
        # An unset ServicePaths field means the plugin never told us
        # where this lives, which is a different fact from the file
        # being absent. Reporting both as "missing" would blame the
        # device for a wiring gap.
        if resolve.paths_field(ctx.paths, spec.pattern) is None:
            return [_unresolved(spec, ctx)]
    elif ctx.root(spec.root) is None:
        return [_unresolved(spec, ctx)]
    matches = resolve.expand(spec, ctx)
    if not matches:
        return [_missing(spec, ctx)]
    if len(matches) == 1:
        return [_stat_record(spec, ctx, matches[0])]
    return [_group_record(spec, ctx, matches)]


def _via(spec: SourceSpec, ctx: BundleContext) -> str:
    """Label describing how this row's path was resolved."""
    if spec.root == "paths":
        return f"ServicePaths.{spec.pattern}"
    return ctx.root_sources.get(spec.root, spec.root)


def _expected_display(spec: SourceSpec, ctx: BundleContext) -> str:
    """The path (or glob) this row was looking for."""
    if spec.root == "paths":
        found = resolve.paths_field(ctx.paths, spec.pattern)
        return str(found) if found else f"<unset: {spec.pattern}>"
    root = ctx.root(spec.root)
    return str(Path(root) / spec.pattern) if root else f"<{spec.root} unresolved>"


def _base(spec: SourceSpec, ctx: BundleContext, status: str) -> PathRecord:
    """Build the common part of every audit record."""
    return PathRecord(
        key=spec.key,
        what=spec.what,
        expected_path=_expected_display(spec, ctx),
        resolved_via=_via(spec, ctx),
        status=status,
        expect=spec.expect,
        action=_ACTION_BY_POLICY.get(spec.policy, spec.policy),
        writer=spec.writer,
        note=spec.note,
    )


def _unresolved(spec: SourceSpec, ctx: BundleContext) -> PathRecord:
    """Record a row whose root could not be resolved at all."""
    record = _base(spec, ctx, "root_unresolved")
    record.action = "skipped"
    return record


def _missing(spec: SourceSpec, ctx: BundleContext) -> PathRecord:
    """Record a row with nothing on disk.

    The action column describes what *happened*, not what the policy
    would have done, so an absent file must not read as "included" —
    that wording implies we shipped something we never found.
    """
    record = _base(spec, ctx, "missing")
    record.action = "absent"
    return record


def _stat_record(
    spec: SourceSpec, ctx: BundleContext, path: Path,
) -> PathRecord:
    """Stat one path. Never opens it, whatever its policy."""
    record = _base(spec, ctx, "missing")
    record.expected_path = str(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        # Absent is not the same as unreadable. FileNotFoundError is an
        # OSError, so catching OSError alone mislabelled every merely
        # absent file as a permissions problem.
        return record
    except OSError as err:
        record.status = "unreadable"
        record.note = f"{spec.note} ({err.strerror})".strip()
        return record
    if path.is_dir():
        record.status = "present_dir"
        record.entries = _count_entries(path)
        record.size = None
    else:
        record.status = "empty" if info.st_size == 0 else "present"
        record.size = info.st_size
    record.mtime = info.st_mtime
    record.mode = oct(info.st_mode & 0o7777)
    return record


def _group_record(
    spec: SourceSpec, ctx: BundleContext, matches: list[Path],
) -> PathRecord:
    """Collapse a multi-match glob row into one summary line."""
    record = _base(spec, ctx, f"present({len(matches)})")
    record.entries = len(matches)
    total = 0
    newest: float | None = None
    for path in matches:
        try:
            info = path.stat()
        except OSError:
            continue
        total += info.st_size
        newest = info.st_mtime if newest is None else max(newest, info.st_mtime)
    record.size = total
    record.mtime = newest
    return record


def _count_entries(path: Path) -> int | None:
    """Count immediate children without descending.

    Deliberately shallow: the point is to show that a bulk directory
    exists and roughly how populated it is, not to walk tens of
    gigabytes of Wine prefix.
    """
    try:
        return sum(1 for _ in path.iterdir())
    except OSError:
        return None


def unexpected_missing(records: list[PathRecord]) -> list[PathRecord]:
    """Rows that are absent but should not be.

    Only ``expect="always"`` rows qualify. Everything else is
    conditional on a store being connected or a sync having run, so
    counting those as problems would make a healthy install look broken.
    """
    return [
        record for record in records
        if record.expect == "always"
        and record.status in ("missing", "root_unresolved", "unreadable")
    ]


def render_audit(ctx: BundleContext, records: list[PathRecord]) -> str:
    """Render the roots block and the audit table as text."""
    lines = ["ROOTS", "-----"]
    for name in sorted(ctx.roots):
        source = ctx.root_sources.get(name, "?")
        lines.append(f"  {name:<12} {ctx.roots[name] or '<unresolved>'}  [{source}]")
    if ctx.dest_candidates:
        lines.append(f"  {'destination':<12} {ctx.dest_dir}  [{ctx.dest_source}]")
        for candidate in ctx.dest_candidates:
            lines.append(f"  {'':<12}   tried: {candidate}")
    lines.extend(["", "PATH AUDIT", "----------"])
    lines.append(_header())
    lines.extend(_row(record) for record in records)
    return "\n".join(lines) + "\n"


def _header() -> str:
    """Column header matching :func:`_row`'s widths."""
    return (
        f"  {'KEY':<24} {'STATUS':<13} {'SIZE':>9}  "
        f"{'MODIFIED':<17} {'EXPECT':<10} {'ACTION':<18} PATH"
    )


def _row(record: PathRecord) -> str:
    """Render one audit record as an aligned line."""
    size = _human(record.size) if record.size is not None else "-"
    if record.entries is not None and record.status.startswith("present_dir"):
        size = f"{record.entries} items"
    stamp = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(record.mtime))
        if record.mtime else "-"
    )
    suffix = f"  (mode {record.mode})" if record.mode else ""
    return (
        f"  {record.key:<24} {record.status:<13} {size:>9}  "
        f"{stamp:<17} {record.expect:<10} {record.action:<18} "
        f"{record.expected_path}{suffix}"
    )


def _human(size: int) -> str:
    """Format a byte count compactly."""
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}G"


def denied_rows(records: list[PathRecord]) -> list[str]:
    """Keys whose resolved path is on the never-read list.

    Cross-check for the layering: a row that is both audited and denied
    should be ``presence_only``. Anything else showing up here means a
    collected row is pointing at a secret, which the collector will
    refuse to read — this makes that visible rather than silent.
    """
    flagged: list[str] = []
    for record in records:
        if record.status in ("missing", "root_unresolved"):
            continue
        denied, pattern = is_denied(Path(record.expected_path))
        if denied and record.action not in ("presence_only", "excluded_bulk"):
            flagged.append(f"{record.key} ({pattern})")
    return flagged
