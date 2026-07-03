"""services.security — Reactive security audit + policy enforcement.

Previously a flat 620 LOC module ``services/security_service.py``;
split on 2026-04-18 during the volumetry refactor into a
subpackage of focused files:

  - ``service``        : ``SecurityService`` facade class
  - ``audit_log``      : ``AuditLog`` (bounded deque + counters)
  - ``bruteforce``     : ``BruteForceDetector`` (Policy 1)
  - ``device_reset``   : machine-id mismatch handler (Policy 3)
  - ``config_readers`` : defensive ConfigManager readers
  - ``bus_emitter``    : fire-and-forget ``Events.SECURITY_*`` emit
  - ``mixins/``        : 4 thematic ``@subscribe`` handler mixins
                         (tokens, permissions, auth, config)

Public API preserved via re-export: callers continue to use
``from unifideck.services.security import SecurityService``
(or ``.security_service`` — kept as a shim during the migration
window; see ``services/service_bootstrap.py``).
"""
from __future__ import annotations

from .service import SecurityService

__all__ = ["SecurityService"]
