"""Security service — audit log + brute-force detection + token surveillance.

OP-19 | py_modules/unifideck/services/security/__init__.py

The security sub-package groups every security-related concern of
the plugin : credential storage permissions, audit-log emission,
brute-force detection on auth attempts, device-reset detection.

This package is referenced by ``ServiceContainer`` as ``security``.
"""

from __future__ import annotations
from .service import SecurityService

__all__ = ["SecurityService"]
