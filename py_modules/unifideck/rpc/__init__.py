"""RPC sub-package — handler classes + composition primitives.

OP-24 | py_modules/unifideck/rpc/__init__.py

This sub-package implements the bridge between the Decky-side
plugin class and the rest of Unifideck. Two cooperating layers:

* **Handlers** (``handlers/``) — concrete RPC method
  implementations, grouped by domain (action, download,
  launch, observability, security, store, ui). Each group
  is a class that takes the same set of injected services
  (bus, registry, cache, config, sync, services) and exposes
  public coroutine methods that the frontend can call.
* **Mixins** (``mixins/``) — legacy / supplementary glue
  that some handlers compose on top of (e.g. shared sync /
  cloud-failure error toasts).

Plus three small primitives at the root:

* ``RpcError``           — typed exception (see ``errors.py``);
* ``rpc_wrapper``        — uniform envelope decorator;
* ``auto_wrap_rpc_methods`` — class-level auto-wrap of every
  public coroutine method.
"""

from .auto_wire import auto_wrap_rpc_methods
from .errors import RpcError
from .wrapper import rpc_wrapper

__all__ = [
    "RpcError",
    "rpc_wrapper",
    "auto_wrap_rpc_methods",
]
