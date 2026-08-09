"""support_bundle/check_kit.py — Shared primitives for the derived checks.

Extracted from ``checks.py`` when that module reached its 550-line cap and a
new check had to go somewhere. Holds only what a check *module* needs to
exist — the read-only view over collected data, and the four verdict
constructors — so sibling check modules (``checks.py``,
``checks_protontricks.py``) can be split by subject without importing each
other.

The contract every check obeys lives here rather than in prose: a check takes
a :class:`View`, returns a :class:`CheckResult`, reads only data that was
already collected, and never touches the filesystem.
"""
from __future__ import annotations

from typing import Any

from .spec import BundleContext, CheckResult, PathRecord


class View:
    """Read-only view over everything the checks need."""

    def __init__(
        self, ctx: BundleContext, records: list[PathRecord], env: dict[str, Any],
    ) -> None:
        """Index the audit by key for constant-time lookups."""
        self.ctx = ctx
        self.env = env
        self.by_key = {record.key: record for record in records}

    def status(self, key: str) -> str:
        """Audit status for ``key``, or "absent" when not audited."""
        record = self.by_key.get(key)
        return record.status if record else "absent"

    def present(self, key: str) -> bool:
        """True when ``key`` resolved to something on disk."""
        return self.status(key).startswith(("present", "empty"))

    def block(self, name: str) -> dict[str, Any]:
        """One environment block, or {} when it failed to build."""
        value = self.env.get(name)
        return value if isinstance(value, dict) else {}


def ok(name: str, detail: str = "") -> CheckResult:
    """A passing verdict."""
    return CheckResult(name=name, status="pass", detail=detail)


def fail(name: str, detail: str) -> CheckResult:
    """A failing verdict — something is broken and needs fixing."""
    return CheckResult(name=name, status="fail", detail=detail)


def warn(name: str, detail: str) -> CheckResult:
    """A suspicious state that is not provably broken."""
    return CheckResult(name=name, status="warn", detail=detail)


def na(name: str, detail: str) -> CheckResult:
    """Not applicable on this machine, or the inputs were not collected."""
    return CheckResult(name=name, status="na", detail=detail)
