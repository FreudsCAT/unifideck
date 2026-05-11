"""GOG store sub-package — public entry point.

OP-50 | py_modules/unifideck/stores/gog/__init__.py

Re-exports ``GOGStore`` so callers can write
``from unifideck.stores.gog import GOGStore``. The class itself lives
in ``store.py`` (OP-50a) and is the only public surface of the entire
sub-package — everything else is internal.

Discovered by ``StoreRegistry.auto_discover()`` via the
``<name>/store.py`` recognition pattern in the registry.
"""

from .store import GOGStore

__all__ = ["GOGStore"]
