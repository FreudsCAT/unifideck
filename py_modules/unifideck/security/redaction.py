"""security/redaction.py — Audit payload redaction.

Defensive helper that strips sensitive values from any payload
about to be persisted in the audit log or forwarded over the
event bus. The audit machinery is purely observational, but the
bar is high — even a single accidental ``access_token=eyJ...``
in a SECURITY_* emit would leak a credential into:

  - the in-memory ``AuditLog`` deque (visible via the
    ``get_audit_log`` RPC);
  - any future telemetry exporter we wire as a bus subscriber;
  - the operator-facing DiagnosticsPanel timeline.

The existing ``audit_emitter`` helpers already document
"MUST NOT contain user data, tokens, or stack traces" but rely
on every future contributor reading and respecting that comment.
This module is the safety net: even if a careless caller passes
``access_token=...`` through, the value never leaves this layer
in plaintext.

Redaction policy
----------------
The implementation is **key-based**, not content-based — the
caller's intent is in the parameter name, not the value shape.
A value bound to a "sensitive" key (any of the patterns in
``_SENSITIVE_KEY_PATTERNS``) is replaced with the literal string
``"<redacted>"``. Other values are passed through, with two
adjustments:

  - dict values are recursed into (nested redaction);
  - long string values (>``_MAX_VALUE_CHARS``) are truncated
    with a length annotation so the audit entry stays bounded
    even if the caller accidentally embeds a JWT in a non-
    sensitive field.

The pattern list intentionally errs on the side of over-
redaction — a few false positives in the audit log (e.g. an
operator's ``token_count`` field being redacted) are far less
costly than a single false negative leaking a real token. If a
legitimate emit needs to surface one of these strings, the
caller renames its kwarg.

The redactor never raises — failures fall back to a hardcoded
sentinel dict so the audit log records *something* even when
the input is malformed (a sub-dict that's actually a list, an
object missing ``__iter__``, etc.). Audit must never break the
code path being observed.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Lower-case substrings checked against each kwarg name. If any
# substring appears in the (lower-cased) key, the value is
# redacted. Patterns are deliberately broad so misspellings or
# composite names ("xbl_token", "user_password_hash") still
# match without per-store tweaks.
_SENSITIVE_KEY_PATTERNS = (
    "token",
    "password",
    "secret",
    "cookie",
    "api_key",
    "apikey",
    "bearer",
    "credential",
    "session_id",
    "auth_code",
    "refresh",
    "access_key",
)

# Cap on string-value length in audit entries. Real fields we
# expect to surface (file paths, store names, error reasons)
# fit comfortably under 256 chars. Anything longer is either a
# JWT, a stack trace, or a binary blob hex-dumped — none of
# which belong in an audit log.
_MAX_VALUE_CHARS = 256

# Replacement value for redacted fields. Operators reading the
# audit log see the literal string ``<redacted>`` rather than
# an empty value, which would be ambiguous with "not provided".
_REDACTED_SENTINEL = "<redacted>"


def redact_for_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with sensitive values stripped.

    Iterates the top-level keys and:

      - replaces values bound to a sensitive key (per
        ``_SENSITIVE_KEY_PATTERNS``) with ``"<redacted>"``;
      - recurses into nested dicts so ``{"creds": {"token": ...}}``
        gets the inner token redacted too;
      - truncates long string values so a stray JWT or stack
        trace can't bloat the log;
      - passes everything else through unchanged.

    Always returns a new dict — never mutates the input. This
    matters because callers (the audit log, the bus emitter)
    forward the original kwargs to multiple subscribers and
    must not let one subscriber's redaction leak into another's
    handler.

    Args:
        payload: The dict that was about to be recorded or
            emitted. May be empty, may contain non-string keys
            (which are tolerated but stringified internally for
            pattern matching), may contain non-serialisable
            values (passed through as-is — the audit layer
            will deal with them via ``str()`` if it needs to).

    Returns:
        A new dict with the same key set as ``payload``,
        every leaf value either preserved, redacted, or
        truncated per the policy above.
    """
    if not isinstance(payload, dict):
        return {"<malformed_payload>": _REDACTED_SENTINEL}  # type: ignore[unreachable]  # defensive fallback for unexpected payload shape
    result: dict[str, Any] = {}
    for key, value in payload.items():
        result[key] = _redact_value(key, value)
    return result


def _redact_value(key: Any, value: Any) -> Any:
    """Apply the redaction rules to one (key, value) pair.

    Extracted so ``redact_for_audit`` stays a tight iteration
    over the dict. The branching here is the actual policy:
    sensitive key → sentinel, dict → recurse, long str →
    truncate, otherwise → identity.
    """
    if _is_sensitive_key(key):
        return _REDACTED_SENTINEL
    if isinstance(value, dict):
        return redact_for_audit(value)
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        head = value[:_MAX_VALUE_CHARS // 2]
        return f"{head}...[truncated {len(value)} chars]"
    return value


def _is_sensitive_key(key: Any) -> bool:
    """Return True if ``key`` matches a sensitive pattern.

    Stringifies the key first so non-str dict keys (rare but
    legal) can't bypass the check via ``"".__contains__``
    raising. Comparison is case-insensitive: ``Token``,
    ``ACCESS_TOKEN`` and ``token`` all match.
    """
    try:
        normalised = str(key).lower()
    except Exception:
        # A pathological key whose str() raises is itself
        # suspicious — treat as sensitive to be safe.
        return True
    return any(p in normalised for p in _SENSITIVE_KEY_PATTERNS)
