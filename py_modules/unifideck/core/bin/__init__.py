"""Bundled CLI resolver + version-signature verification.

OP-08d | py_modules/unifideck/core/bin/__init__.py

Stores rely on external CLI tools (Legendary for Epic, Nile
for Amazon, etc.) bundled with the plugin. This sub-package
owns:

* ``binary_resolver`` (class + singleton instance) — locates
  bundled binaries on disk and caches the resolution.
* ``binary_signatures`` — SHA-256 verification of bundled
  binaries against a baked-in manifest (defence against
  partial / corrupted plugin installs).
* ``cli_timeouts``      — per-CLI timeout reader (from
  config), so each tool gets a tailored budget.
"""

from .binary_resolver import (
    BinaryResolver,
    binary_resolver,
)
from .binary_signatures import (
    compute_sha256,
    verify_bundled_binary,
)
from .cli_timeouts import read_cli_timeouts

__all__ = [
    "BinaryResolver",
    "binary_resolver",
    "compute_sha256",
    "read_cli_timeouts",
    "verify_bundled_binary",
]
