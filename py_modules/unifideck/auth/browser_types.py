"""auth/browser_types.py — Shared result type for OAuth captures.

Extracted from ``auth/browser.py`` in lot 13a (file-cap split):
the OAuth monitor module grew past the 550-line volumetry gate
after the cognitive-complexity refactor. Splitting the pure
data-shape into its own module both respects the gate and
makes the type independently importable for tests and callers
that only need the return shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuthCaptureResult:
    """Outcome of an OAuth redirect capture attempt."""

    success: bool
    redirect_url: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def code(self) -> str | None:
        """Convenience: return the ``code`` query parameter if any."""
        return self.params.get("code")

    @property
    def state(self) -> str | None:
        """Convenience: return the ``state`` query parameter if any."""
        return self.params.get("state")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict for RPC return values."""
        return {
            "success": self.success,
            "redirect_url": self.redirect_url,
            "params": dict(self.params),
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }
