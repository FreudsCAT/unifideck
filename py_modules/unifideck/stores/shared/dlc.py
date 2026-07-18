import logging

logger = logging.getLogger(__name__)
_DLC_SUPPORTED_STORES = {"epic", "gog"}
def get_dlc_flags(store: str, with_dlc: bool = True) -> list[str]:
    """Return the legendary/gogdl DLC flag for ``store``.

    ``with_dlc=True`` → ``--with-dlcs`` (install DLC alongside the base
    game); ``with_dlc=False`` → an explicit ``--skip-dlcs`` (needed
    because, with ``--yes``, merely omitting ``--with-dlcs`` still lets
    the tool auto-install DLC). Empty for stores without DLC support.
    """
    if store.lower() not in _DLC_SUPPORTED_STORES:
        return []
    return ["--with-dlcs"] if with_dlc else ["--skip-dlcs"]
def store_supports_dlc(store: str) -> bool:
    """Store supports dlc."""
    return store.lower() in _DLC_SUPPORTED_STORES
