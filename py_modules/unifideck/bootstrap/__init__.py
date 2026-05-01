"""bootstrap — Plugin lifecycle helpers extracted from main.py.

This subpackage groups the functions that orchestrate plugin
boot and teardown. Each function takes the Plugin instance as
its first argument and mutates its attributes in place so the
exact ordering of ``_main`` is preserved — reshuffling the
sequence via return-value composition would change observable
behavior (services that subscribe to bus events during their
``__init__`` depend on strict ordering).

Contract:
  - Every public function here takes ``plugin`` as the first
    positional argument and either mutates it or reads from it.
  - Functions are synchronous unless they genuinely need to
    ``await`` something (``boot_plugin`` is async because
    service bootstrap does I/O; ``register_default_caches``
    is synchronous).
  - No mixin imports — this subpackage is bootstrap-level, it
    shouldn't touch the RPC surface.
"""
