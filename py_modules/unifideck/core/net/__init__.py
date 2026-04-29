"""core/net — Network helpers (SSL contexts, HTTP wrappers)."""
from .ssl_helpers import ssl_ctx_permissive, ssl_ctx_strict

__all__ = ["ssl_ctx_permissive", "ssl_ctx_strict"]
