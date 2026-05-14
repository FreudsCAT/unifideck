from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

_ENV_TOKEN_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
_LSFG_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)$")
@dataclass
class ParsedOptions:
    """Parsed options."""
    wrappers: list[str] = field(default_factory=list)
    game_args: list[str] = field(default_factory=list)
    env_overrides: dict[str, str] = field(default_factory=dict)
    lsfg_requested: bool = False
def parse_launch_options(raw: str) -> ParsedOptions:
    """Parse launch options."""
    result = ParsedOptions()
    if not raw or not raw.strip():
        return result
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    remaining: list[str] = []
    for tok in tokens:
        m = _ENV_TOKEN_RE.match(tok)
        if m:
            result.env_overrides[m.group(1)] = m.group(2)
        else:
            remaining.append(tok)
    home = os.path.expanduser("~")
    lsfg_filtered: list[str] = []
    for tok in remaining:
        expanded = (
            tok.replace("~", home, 1)
            if tok.startswith("~") else tok
        )
        if expanded.endswith("/lsfg"):
            result.lsfg_requested = True
        else:
            lsfg_filtered.append(tok)
    if result.env_overrides.get("LSFG") == "1":
        result.lsfg_requested = True
    if result.env_overrides.get("ENABLE_LSFG") == "1":
        result.lsfg_requested = True
    _split_tokens_around_command(lsfg_filtered, result)
    return result

def _split_tokens_around_command(
    tokens: list[str], result: ParsedOptions,
) -> None:

    """Split tokens around command."""
    found_cmd = False
    for tok in tokens:
        if tok == "%command%":
            found_cmd = True
            continue
        if tok == "#%command%":
            continue
        if found_cmd:
            result.game_args.append(tok)
        else:
            result.wrappers.append(tok)
    if (
        not found_cmd
        and result.wrappers
        and not result.game_args
    ):
        result.game_args = result.wrappers
        result.wrappers = []
def _parse_lsfg_export_line(line: str) -> tuple[str, str] | None:
    """Parse a single ``export KEY=VALUE`` line from an LSFG script.

    Returns ``(key, value)`` if the line is a valid export of an
    LSFG-prefixed variable, otherwise ``None``. Handles single-
    and double-quoted values by stripping the surrounding quotes.
    Anything else (comments, shebangs, ``exec``, non-export, keys
    that don't match the LSFG filter) returns ``None`` so the
    caller can ``continue`` cleanly.
    """
    line = line.strip()
    if not line or line.startswith(("#", "#!")):
        return None
    if line.startswith("exec ") or not line.startswith("export "):
        return None
    kv = line[len("export "):]
    if "=" not in kv:
        return None
    key, _, value = kv.partition("=")
    key = key.strip()
    value = value.strip()
    # Strip a single layer of matching quotes; this matches the
    # shell's basic dequoting and is enough for the export lines
    # the LSFG launcher script produces.
    if len(value) >= 2 and (
        (value[0] == '"' and value[-1] == '"')
        or (value[0] == "'" and value[-1] == "'")
    ):
        value = value[1:-1]
    if not _LSFG_KEY_RE.match(key):
        return None
    return key, value


def apply_lsfg_env(
    opts: ParsedOptions,
    lsfg_script: Path | None = None,
) -> dict[str, str]:
    """Build the env overlay for an LSFG-wrapped launch.

    Returns ``{"ENABLE_LSFG": "1", <LSFG_*>: <value>, ...}`` —
    empty dict when LSFG was not requested or the script isn't on
    disk. Parses ``export`` lines from ``~/lsfg`` (or the supplied
    ``lsfg_script`` path, used by tests) and applies only keys
    that pass :data:`_LSFG_KEY_RE`.
    """
    if not opts.lsfg_requested:
        return {}
    if lsfg_script is None:
        lsfg_script = Path(os.path.expanduser("~/lsfg"))
    if not lsfg_script.is_file():
        return {}
    overlay: dict[str, str] = {"ENABLE_LSFG": "1"}
    try:
        content = lsfg_script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # We can't read the script (permissions, racing rotate),
        # but the user did ask for LSFG — return the enable flag
        # so the launcher can at least try with default values.
        return overlay
    for raw_line in content.splitlines():
        parsed = _parse_lsfg_export_line(raw_line)
        if parsed is not None:
            key, value = parsed
            overlay[key] = value
    return overlay
