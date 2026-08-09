"""support_bundle/checks_protontricks.py — Is Protontricks usable verdict.

Companion to ``probe_protontricks``, which collects the state; this turns it
into one line at the top of ``diagnostics.txt``. Split out of ``checks.py``
because that module is at its size cap, and because this verdict has real
reasoning behind it: three independent things must hold before Protontricks
works, and naming *which* one failed is the whole value.

The failure order matters and is preserved below — it is the order
Protontricks itself hits them:

1. it must find the **prefix** (``compatdata/<appid>/pfx`` a dir, ``pfx.lock``
   a file beside it), or it skips the game with "does not have a prefix";
2. it must find the **Proton** — and a compat-tool bridge the sandbox can read
   but not *search* is a silent failure that looks configured;
3. failing either, it prints its own error, which we quote verbatim rather
   than paraphrase.
"""
from __future__ import annotations

from typing import Any

from .check_kit import View, na, ok, warn
from .spec import CheckResult

_NAME = "protontricks_usable"
_MAX_LISTED_APPIDS = 5


def check_protontricks(view: View) -> CheckResult:
    """Verdict on whether Protontricks can actually reach our games."""
    block = view.block("protontricks")
    if not block:
        return na(_NAME, "protontricks state not collected")
    if block.get("distribution", {}).get("primary") == "absent":
        return na(_NAME, "no Protontricks installed")
    problems = _problems(block)
    if problems:
        return warn(_NAME, "; ".join(problems))
    return ok(_NAME, _summary(block))


def _problems(block: dict[str, Any]) -> list[str]:
    """Every reason Protontricks would fail, in the order it hits them."""
    problems: list[str] = []
    if broken := _broken_bridges(block):
        shown = ", ".join(broken[:_MAX_LISTED_APPIDS])
        problems.append(
            f"{len(broken)} prefix bridge(s) fail protontricks' gates "
            f"(appids {shown}) - it will skip those games",
        )
    if detail := _tool_search_problem(block):
        problems.append(detail)
    if detail := _reported_error(block):
        problems.append(detail)
    return problems


def _broken_bridges(block: dict[str, Any]) -> list[str]:
    """Appids whose bridge exists but fails Protontricks' two gates."""
    bridges = block.get("prefix_bridge", {}).get("bridges") or []
    return [
        str(row.get("appid"))
        for row in bridges
        if not (row.get("pfx_is_dir") and row.get("pfx_lock_is_file"))
    ]


def _tool_search_problem(block: dict[str, Any]) -> str:
    """Report a compat-tool bridge the sandbox cannot search, else ""."""
    tools = block.get("compat_tool_bridge", {})
    access = tools.get("sandbox_access")
    if not tools.get("links") or access not in ("partial", "absent"):
        return ""
    return (
        f"compat-tool links exist but sandbox_access={access} - protontricks "
        "cannot search them, so it reports 'Active Proton installation could "
        "not be found automatically'"
    )


def _reported_error(block: dict[str, Any]) -> str:
    """Quote Protontricks' own resolution error, else ""."""
    stderr = (block.get("listing") or {}).get("stderr") or ""
    lines = [
        line for line in stderr.splitlines()
        if "could not be found" in line or "Could not find" in line
    ]
    return f"protontricks -l reported: {lines[0].strip()}" if lines else ""


def _summary(block: dict[str, Any]) -> str:
    """One line naming the distribution and what it can currently see."""
    primary = block.get("distribution", {}).get("primary", "?")
    bridges = block.get("prefix_bridge", {}).get("bridges") or []
    links = block.get("compat_tool_bridge", {}).get("links") or []
    return (
        f"{primary}; {len(bridges)} prefix bridge(s), "
        f"{len(links)} compat-tool link(s)"
    )
