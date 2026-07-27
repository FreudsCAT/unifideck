"""Amazon Games install-progress line parsing (nile stdout).

Extracted verbatim from ``amazon_install.py`` when that module crossed the
volumetry cap. Mirrors the existing ``stores/gog/install/progress.py``
split, which carries the same three parsers for gogdl.

Nile's ProgressBar emits lines like::

    = Progress: 42.50 123456789/987654321, Running for: 00:01:30, ETA: 00:01:28

Kept store-local rather than folded into
``stores/shared/cli_install_helpers`` — the shared ``parse_eta_seconds`` /
``parse_speed_bps`` are close but not byte-identical in their tokenising
(nile's sign handling differs), and the progress bar is the one surface a
silent parsing regression would not fail a test on.
"""

from __future__ import annotations

from typing import Any


def parse_eta(line: str) -> int | None:
    """Parse eta from nile line."""
    if "ETA:" not in line:
        return None
    try:
        eta_part = line.split("ETA:", 1)[1].strip()
        if not eta_part:
            return None
        eta_time = eta_part.split()[0]
        parts = eta_time.split(":")
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            return m * 60 + s
    except (ValueError, IndexError):
        return None
    return None


def parse_speed_mib(line: str) -> float | None:
    """Parse speed from nile line."""
    if "Download" not in line or "MiB/s" not in line:
        return None
    try:
        tail = line.split("Download", 1)[1]
        speed_part = tail.split("MiB/s", 1)[0].strip()
        speed_part = speed_part.lstrip("-").strip()
        speed_tokens = speed_part.split()
        if not speed_tokens:
            return None
        return float(speed_tokens[-1]) * 1024 * 1024
    except (ValueError, IndexError):
        return None


def parse_progress_line(line: str, progress: dict[str, Any]) -> bool:
    """Parse progress percent and bytes from nile line.

    Mutates ``progress`` in place; returns True when the line carried a
    usable update (so the caller knows to emit), False otherwise.
    """
    speed_bps = parse_speed_mib(line)
    if speed_bps is not None:
        progress["speed_bps"] = speed_bps
        return True
    if "Progress:" not in line:
        return False
    try:
        part = line.split("Progress:", 1)[1].strip()
        tokens = part.split()
        if len(tokens) < 2:
            return False
        progress["progress_percent"] = float(tokens[0])
        bytes_part = tokens[1].rstrip(",")
        if "/" not in bytes_part:
            return True
        written, total = bytes_part.split("/", 1)
        progress["downloaded_bytes"] = int(written)
        progress["total_bytes"] = int(total)
        eta = parse_eta(line)
        if eta is not None:
            progress["eta_seconds"] = eta
        return True
    except (ValueError, IndexError):
        return False
