"""services/bootstrap — Service container wiring and lifecycle.

Re-exports the five public entry points used by the plugin bootstrap
and launcher subsystems so callers can do
``from unifideck.services.bootstrap import ServicePaths`` instead of
reaching into the internal submodule layout (``paths``,
``constructor``, ``startup``, ``teardown``).

The split across submodules exists for readability — ``paths.py``
holds the ``ServicePaths`` dataclass, ``constructor.py`` builds the
container synchronously, ``startup.py``/``teardown.py`` manage the
async lifecycle — but the public API is flat.
"""

from __future__ import annotations

from .constructor import bootstrap_services, build_service_subset
from .paths import ServicePaths
from .startup import start_async_services
from .store_injector import inject_store_dependencies
from .teardown import stop_all_services

__all__ = [
    "ServicePaths",
    "bootstrap_services",
    "build_service_subset",
    "inject_store_dependencies",
    "start_async_services",
    "stop_all_services",
]
