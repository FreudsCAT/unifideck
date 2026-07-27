"""py_modules/unifideck/core/binaries/ — External CLI tool lifecycle.

Consolidated. This subpackage owns the complete
lifecycle management of the external CLI helpers Unifideck
depends on (legendary for Epic, nile for Amazon, gogdl for GOG,
and any future additions):

  - binary_resolver : 3-tier locator that finds a CLI tool
                          on disk. Searches, in order: declared
                          ``CLITool.search_paths`` (e.g. the
                          plugin's bundled ``bin/`` directory),
                          the system ``PATH``, and
                          ``~/.local/bin/<name>``. Exposes both
                          the ``BinaryResolver`` class and a
                          module-level ``binary_resolver``
                          singleton for the hot path.

  - binary_signatures : SHA-256 allowlist for bundled binaries.
                          ``verify_bundled_binary(name)`` hashes
                          the file at plugin startup and refuses
                          to return it to stores if it doesn't
                          match the known-good hash. Scope is
                          intentionally limited to bundled tools
                          — system binaries trusted by the OS
                          package manager are out of scope.

  - cli_timeouts : ``read_cli_timeouts(config)`` returns
                          the per-operation timeout dict
                          (``auth_check``, ``version_check``,
                          ``library_fetch``, ``install_poll``,
                          ``uninstall``) by reading the
                          ``cli_timeouts.*`` block from
                          ConfigManager with sensible fallback
                          defaults. Stores capture the result
                          once in their constructor so hot paths
                          don't re-parse config on every call.

The three modules form one coherent story — find the tool, trust
the tool, run the tool — and every concrete store at some point
touches all three. Colocating them documents that coupling.

Clean break: the previous locations unifideck.core.binary_resolver,
unifideck.core.binary_signatures and unifideck.core.cli_timeouts
no longer exist. Every callsite has been rewritten as part of
. If you hit an ``ImportError`` on any of those, you
are on a pre-17f checkout.

Typical usage::

    from unifideck.core.binaries import (
        binary_resolver, # singleton
        read_cli_timeouts, # config reader
        verify_bundled_binary, # integrity check
    )
"""
from .binary_resolver import (
    BinaryResolver,
    binary_resolver,
)
from .binary_signatures import (
    compute_sha256,
    verify_bundled_binary,
)
from .cli_env import (
    SCRUBBED_VARS,
    clean_cli_env,
    scrub_cli_env,
)
from .cli_timeouts import read_cli_timeouts

__all__ = [
    # cli_env.py
    "SCRUBBED_VARS",
    # binary_resolver.py
    "BinaryResolver",
    "binary_resolver",
    "clean_cli_env",
    # binary_signatures.py
    "compute_sha256",
    # cli_timeouts.py
    "read_cli_timeouts",
    "scrub_cli_env",
    "verify_bundled_binary",
]
