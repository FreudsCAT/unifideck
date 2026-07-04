"""services/shortcut — Steam shortcuts management service.

Re-exports ``ShortcutService`` so callers can write
``from unifideck.services.shortcut import ShortcutService``
rather than reaching into the private ``service`` submodule.
This is consumed by every store (Epic / GOG / Ubisoft / Amazon /
Microsoft) so the import is a hot path for plugin startup.
"""

from __future__ import annotations

from .service import ShortcutService

__all__ = ["ShortcutService"]
