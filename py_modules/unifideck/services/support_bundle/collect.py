"""support_bundle/collect.py — Build the archive.

Walks the registry in priority order, reads what it is allowed to read,
scrubs it, and writes one zip. Priority is also the budget order, so if
the total cap is ever reached it is the least useful artifacts that get
dropped, never the generated reports or the newest logs.

Two invariants shape this module:

**A partial bundle beats no bundle.** Every per-source read is guarded
and failures become manifest entries, so one unreadable log cannot
abort the capture. The only hard failures are "nowhere writable to put
the zip" and "the zip itself could not be opened".

**Nothing half-written reaches the user's Downloads folder.** The
archive is built as a hidden ``.part`` file and atomically renamed on
success; any failure unlinks it.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from . import (
    checks,
    env_report,
    inventory,
    path_audit,
    probe_plugin_logs,
    resolve,
    scrub,
)
from .deny import deny_patterns, is_denied
from .sources import COLLECTED
from .spec import (
    CAP_TOTAL_UNCOMPRESSED,
    TRUNCATION_BANNER,
    BundleContext,
    CheckResult,
    EntryRecord,
    PathRecord,
    SkipRecord,
    SourceSpec,
)

logger = logging.getLogger(__name__)

_COMPRESS_LEVEL = 6
# Below this much free space we halve the budget rather than fail: a
# smaller bundle is still a useful bundle.
_LOW_DISK_FACTOR = 4


class _Run:
    """Mutable state for one capture."""

    def __init__(self, archive: zipfile.ZipFile, ctx: BundleContext) -> None:
        """Bind the open archive and initialise the counters."""
        self.archive = archive
        self.ctx = ctx
        self.entries: list[EntryRecord] = []
        self.skipped: list[SkipRecord] = []
        self.errors: list[dict[str, str]] = []
        self.warnings: list[str] = []
        self.written: set[str] = set()
        self.budget = CAP_TOTAL_UNCOMPRESSED
        self.used = 0
        self.redactions = 0
        self.lines_dropped = 0

    def remaining(self) -> int:
        """Bytes of uncompressed payload still allowed."""
        return max(0, self.budget - self.used)

    def write(self, arch_path: str, data: bytes) -> None:
        """Add one member and charge it against the budget."""
        self.archive.writestr(arch_path, data)
        self.written.add(arch_path)
        self.used += len(data)

    def skip(self, key: str, reason: str, **extra: Any) -> None:
        """Record a source that was not collected."""
        self.skipped.append(SkipRecord(key=key, reason=reason, **extra))


def _build_context(
    dest_path: str, config: Any, paths: Any, extra: dict[str, Any] | None,
) -> BundleContext:
    """Resolve every root and the destination directory."""
    roots, sources, decky_tried = resolve.build_roots(config, paths)
    dest_dir, dest_source, tried = resolve.resolve_dest(dest_path, config, paths)
    ctx = BundleContext(
        roots=roots, root_sources=sources, dest_dir=str(dest_dir),
        dest_source=dest_source, dest_candidates=tried,
        config=config, paths=paths, extra=extra or {},
    )
    ctx.extra.setdefault("decky_log_dir_candidates", decky_tried)
    return ctx


def _read_capped(path: Path, cap: int, tail: bool) -> tuple[bytes, int, bool]:
    """Read at most ``cap`` bytes, from the end when ``tail``.

    Tailing is the right default for everything here: these files are
    append-only and the failure being reported is at the end. A head
    truncation of a session log yields the boot banner and nothing
    about the crash.

    The first partial line of a tail is dropped so JSONL stays parseable
    and text stays readable — but only when there *is* a line break to
    align to. A window with no newline at all (one very long line, or a
    single-line file) used to partition down to zero bytes and collect a
    banner reading "kept 0" instead of the content. Some game logs are
    exactly that shape, so the alignment is now best-effort.
    """
    size = path.stat().st_size
    if size <= cap:
        return path.read_bytes(), size, False
    with path.open("rb") as handle:
        if tail:
            handle.seek(size - cap)
            data = handle.read()
            head, newline, rest = data.partition(b"\n")
            data = rest if newline else head
        else:
            data = handle.read(cap)
    banner = TRUNCATION_BANNER.format(kept=len(data), total=size).encode("utf-8")
    return banner + data, size, True


def _arch_path(run: _Run, spec: SourceSpec, path: Path) -> str:
    """Archive-internal path for one source file.

    Nested literal patterns get their parent directory folded into the
    name, because several stores use the same filename: legendary and
    nile both ship an ``installed.json``, and flattening both to
    ``config/installed.json`` silently produced a duplicate zip member.
    A final collision guard covers any future case.
    """
    base = path.name
    parent = Path(spec.pattern).parent.name
    # Only fold the parent in when it actually disambiguates. It does
    # for ~/.config/{legendary,nile}/installed.json; it does not for
    # steam/logs/compat_log.txt landing in a "steam-logs" directory,
    # where it just produced "steam-logs/logs-compat_log.txt".
    if parent and parent not in (".", "..") and parent not in spec.arch_dir:
        base = f"{parent}-{base}"
    name = resolve.sanitize_entry_name(base)
    arch = f"{spec.arch_dir}/{name}" if spec.arch_dir else name
    if arch in run.written:
        arch = f"{spec.arch_dir}/{spec.key}-{name}" if spec.arch_dir else f"{spec.key}-{name}"
    return arch


def _collect_one(run: _Run, spec: SourceSpec, path: Path) -> None:
    """Read, scrub and add a single file. Never raises."""
    denied, pattern = is_denied(path)
    if denied:
        run.skip(spec.key, "denied_secret", path=str(path), note=f"matched {pattern}")
        return
    try:
        info = path.stat()
    except FileNotFoundError:
        # A registry row pointing at a file this device does not have.
        # Normal for anything store- or feature-conditional, so it is a
        # skip, not an error — the audit records whether it should have
        # been there.
        run.skip(spec.key, "missing", path=str(path))
        return
    except OSError as err:
        run.errors.append({"key": spec.key, "error": repr(err)})
        return
    if info.st_size == 0:
        run.skip(spec.key, "empty", path=str(path), size_bytes=0)
        return
    if spec.scrub == "json" and info.st_size > spec.max_bytes:
        run.skip(
            spec.key, "over_cap", path=str(path), size_bytes=info.st_size,
            note=f"cap {spec.max_bytes}; a truncated JSON document is worse "
                 "than an absent one",
        )
        return
    _add_file(run, spec, path, info.st_size)


def _add_file(run: _Run, spec: SourceSpec, path: Path, size: int) -> None:
    """Read one file within budget, scrub it, and write it."""
    cap = min(spec.max_bytes, run.remaining())
    if cap <= 0:
        run.skip(spec.key, "total_budget_exhausted", path=str(path), size_bytes=size)
        return
    try:
        raw, source_size, truncated = _read_capped(path, cap, spec.policy == "tail")
    except (OSError, ValueError, MemoryError) as err:
        run.errors.append({"key": spec.key, "error": repr(err)})
        return
    data, hits, dropped = scrub.apply_profile(raw, spec.scrub)
    arch_path = _arch_path(run, spec, path)
    run.write(arch_path, data)
    run.redactions += hits
    run.lines_dropped += dropped
    run.entries.append(EntryRecord(
        key=spec.key, arch_path=arch_path, source_path=str(path),
        source_name=path.name, bytes_source=source_size,
        bytes_written=len(data), truncated=truncated,
        mtime=_mtime(path), scrub=spec.scrub,
        redactions=hits, lines_dropped=dropped,
    ))


def _mtime(path: Path) -> float | None:
    """Modification time, or None when it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _collect_files(run: _Run) -> None:
    """Walk the collected registry rows in priority order.

    The newest-N cap is applied here rather than during path expansion
    so that (a) the audit still reports how many files really exist,
    and (b) anything the cap excludes is written into the manifest
    instead of vanishing.
    """
    for spec in sorted(COLLECTED, key=lambda item: item.priority):
        matches = resolve.expand(spec, run.ctx)
        if not matches:
            run.skip(spec.key, "missing")
            continue
        kept, dropped = resolve.split_newest(matches, spec.newest_n)
        for path in dropped:
            run.skip(
                spec.key, "older_than_newest_n", path=str(path),
                note=f"kept the {spec.newest_n} newest of {len(matches)}",
            )
        for path in kept:
            _collect_one(run, spec, path)


def _write_generated(
    run: _Run,
    report: dict[str, Any],
    records: list[PathRecord],
    results: list[CheckResult],
) -> None:
    """Write the human-readable report.

    Written *after* collection so the header can state exactly how
    complete the bundle is. A reader should not have to cross-reference
    the manifest to learn that something was left out.
    """
    diagnostics = "\n".join([
        _banner(run.ctx),
        _completeness(run),
        checks.render_checks(results),
        path_audit.render_audit(run.ctx, records),
        "ENVIRONMENT",
        "-----------",
        env_report.render_text(report),
    ])
    run.write("diagnostics.txt", diagnostics.encode("utf-8"))
    run.write("environment.json", _dump(report))
    run.write("inventory.txt", _inventory(run.ctx).encode("utf-8"))
    _write_sibling_plugin_logs(run)


def _write_sibling_plugin_logs(run: _Run) -> None:
    """Add the other plugins' newest logs, scrubbed like any log of ours.

    Guarded and last: these are third-party files, and a plugin that writes
    something unreadable must cost us the artifact, not the bundle.
    """
    try:
        text, _ = probe_plugin_logs.render_sibling_logs()
        data, hits, dropped = scrub.apply_profile(text.encode("utf-8"), "text")
        run.redactions += hits
        run.lines_dropped += dropped
        run.write("plugins/other-plugin-logs.txt", data)
    except Exception as err:
        logger.debug("[support_bundle] sibling plugin logs failed", exc_info=True)
        run.errors.append({
            "key": "sibling_plugin_logs", "error": repr(err),
        })


def _inventory(ctx: BundleContext) -> str:
    """Enumerate what exists on the device but was not collected.

    Feeds the configured install locations in so the inventory can show
    which games are actually on disk, which no other artifact reveals.
    """
    try:
        from unifideck.utils.paths import get_all_game_directories

        install_dirs = get_all_game_directories(ctx.config)
    except Exception:
        logger.debug("[support_bundle] install dirs unavailable", exc_info=True)
        install_dirs = []
    return inventory.build_inventory(ctx, install_dirs)


def _completeness(run: _Run) -> str:
    """State up front whether anything was left out."""
    truncated = [item for item in run.entries if item.truncated]
    excluded = [
        item for item in run.skipped
        if item.reason in ("older_than_newest_n", "over_cap", "total_budget_exhausted")
    ]
    lines = [
        "COMPLETENESS",
        "------------",
        (f"  {len(run.entries)} file(s) collected, "
         f"{run.used:,} bytes before compression"),
    ]
    if not truncated and not excluded and not run.errors:
        lines.append("  Nothing was truncated, excluded by a size cap, or unreadable.")
    for entry in truncated:
        lines.append(
            f"  TRUNCATED  {entry.arch_path} "
            f"(kept {entry.bytes_written:,} of {entry.bytes_source:,} bytes, tail)",
        )
    for skip in excluded:
        lines.append(f"  EXCLUDED   {skip.key}: {skip.reason} {skip.path}")
    for failure in run.errors:
        lines.append(f"  UNREADABLE {failure['key']}: {failure['error']}")
    return "\n".join(lines) + "\n"


def _banner(ctx: BundleContext) -> str:
    """Header telling the reader what this file is and where to start."""
    return (
        "UNIFIDECK DIAGNOSTIC BUNDLE\n"
        "===========================\n"
        f"Generated : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Written to: {ctx.dest_dir} [{ctx.dest_source}]\n"
        "\n"
        "Read the sanity checks first, then the path audit. Credential\n"
        "files are audited for existence only - their contents are never\n"
        "read or included. See manifest.json for the machine-readable\n"
        "version of everything below.\n"
    )


def _dump(payload: Any) -> bytes:
    """Serialise to pretty JSON, falling back to repr on odd types."""
    return json.dumps(payload, indent=2, sort_keys=True, default=repr).encode("utf-8")


def _write_manifest(
    run: _Run, records: list[PathRecord], results: list[CheckResult],
) -> None:
    """Write the machine-readable manifest. Always written last."""
    payload = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dest": {
            "path": run.ctx.dest_dir,
            "source": run.ctx.dest_source,
            "candidates_tried": run.ctx.dest_candidates,
        },
        "roots": run.ctx.roots,
        "root_sources": run.ctx.root_sources,
        "policy": {
            "total_uncompressed_cap": run.budget,
            "tail_rationale": (
                "logs are append-only, so the reported failure is at the end"
            ),
            "json_over_cap_is_skipped_not_truncated": True,
            "home_paths_preserved": True,
            "credentials_are_presence_only": True,
            "scrub_profiles": scrub.profile_rules(),
            "denied_patterns": deny_patterns(),
        },
        "entries": [vars(item) for item in run.entries],
        "skipped": [vars(item) for item in run.skipped],
        "paths": [vars(item) for item in records],
        "checks": [vars(item) for item in results],
        "errors": run.errors,
        "warnings": run.warnings,
        "counters": {
            "redactions": run.redactions,
            "lines_dropped": run.lines_dropped,
            "uncompressed_bytes": run.used,
        },
    }
    run.write("manifest.json", _dump(payload))


def _apply_disk_budget(run: _Run) -> None:
    """Shrink the budget when the destination is nearly full."""
    try:
        free = shutil.disk_usage(run.ctx.dest_dir).free
    except OSError:
        return
    if free < CAP_TOTAL_UNCOMPRESSED // _LOW_DISK_FACTOR:
        run.budget = CAP_TOTAL_UNCOMPRESSED // 2
        run.warnings.append("low_disk_reduced_budget")


def _result(
    run: _Run,
    target: Path,
    started: float,
    records: list[PathRecord],
    results: list[CheckResult],
) -> dict[str, Any]:
    """Build the RPC payload describing the finished bundle."""
    return {
        "archive_path": str(target),
        "archive_name": target.name,
        "bytes": _size_of(target),
        "uncompressed_bytes": run.used,
        "file_count": len(run.archive.namelist()),
        "dest_source": run.ctx.dest_source,
        "dest_candidates_tried": run.ctx.dest_candidates,
        "truncated": [
            {"entry": item.arch_path, "bytes_source": item.bytes_source,
             "bytes_written": item.bytes_written}
            for item in run.entries if item.truncated
        ],
        "skipped": [
            {"key": item.key, "reason": item.reason} for item in run.skipped
        ],
        "errors": run.errors,
        "warnings": run.warnings,
        "audited": len(records),
        "missing_unexpected": len(path_audit.unexpected_missing(records)),
        "checks_failed": checks.failed_count(results),
        "redactions": run.redactions,
        "lines_dropped": run.lines_dropped,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "in_progress": False,
    }


def _size_of(path: Path) -> int:
    """Size of the finished archive, 0 if it vanished."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _fill_archive(run: _Run) -> tuple[list[PathRecord], list[CheckResult]]:
    """Write every member. The manifest goes last, so it can describe
    everything that came before it."""
    report = env_report.build_environment_report(run.ctx)
    records = path_audit.audit_paths(run.ctx)
    results = checks.run_checks(run.ctx, records, report)
    _collect_files(run)
    _write_generated(run, report, records, results)
    _write_manifest(run, records, results)
    return records, results


def _target_paths(ctx: BundleContext, dest_path: str) -> tuple[Path, Path]:
    """Return the final archive path and the hidden ``.part`` path."""
    dest_dir = Path(ctx.dest_dir)
    target = resolve.unique_path(dest_dir, resolve.archive_name(dest_path))
    return target, dest_dir / f".{target.name}.part"


def _write_archive(
    ctx: BundleContext, part: Path, target: Path, started: float,
) -> dict[str, Any]:
    """Build the archive at ``part`` and describe the result."""
    with zipfile.ZipFile(
        part, "w", zipfile.ZIP_DEFLATED, compresslevel=_COMPRESS_LEVEL,
    ) as archive:
        run = _Run(archive, ctx)
        _apply_disk_budget(run)
        records, results = _fill_archive(run)
        return _result(run, target, started, records, results)


def capture_bundle(
    dest_path: str = "",
    config: Any = None,
    paths: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect everything into one zip and return its description.

    Raises:
        OSError: only when no destination is writable or the archive
            itself cannot be created. Every other failure is reported
            inside the returned payload.
    """
    started = time.monotonic()
    ctx = _build_context(dest_path, config, paths, extra)
    target, part = _target_paths(ctx, dest_path)
    try:
        payload = _write_archive(ctx, part, target, started)
        os.replace(part, target)
    except Exception:
        # Never leave a half-written archive in the user's Downloads.
        part.unlink(missing_ok=True)
        raise
    payload["bytes"] = _size_of(target)
    logger.info(
        "[support_bundle] wrote %s (%s bytes, %s files)",
        target, payload["bytes"], payload["file_count"],
    )
    return payload
