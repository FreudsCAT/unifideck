"""Bootstrap sub-package — plugin start-up orchestration.

OP-13 | py_modules/unifideck/services/bootstrap/__init__.py

Re-exports the small surface a caller (typically ``Plugin._main`` in
the entry-point ``main.py``) needs : ``boot_plugin``, ``unload_plugin``,
``ServiceContainer``, ``ServicePaths``.

The bootstrap sub-package is split into single-responsibility modules:

* ``paths`` (OP-13a) — derive every Layer-1 path from the plugin root;
* ``container`` (OP-13b) — the typed service registry;
* ``service_defs`` (OP-13c) — the table of services and their constructors;
* ``constructor`` (OP-13d) — the entry-point that wires everything up;
* ``startup`` (OP-13e) — async start-up tasks (DB warmup, etc.);
* ``teardown`` (OP-13f) — symmetric shutdown;
* ``store_injector`` (OP-13g) — post-discovery store wiring.
"""

from __future__ import annotations
from .constructor import bootstrap_services, build_service_subset
from .container import ServiceContainer
from .paths import ServicePaths
from .startup import start_async_services
from .store_injector import inject_store_dependencies
from .teardown import stop_all_services

__all__ = [
    "ServiceContainer",
    "ServicePaths",
    "bootstrap_services",
    "build_service_subset",
    "inject_store_dependencies",
    "start_async_services",
    "stop_all_services",
]
