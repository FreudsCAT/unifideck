import logging
logger = logging.getLogger(__name__)
_DLC_SUPPORTED_STORES = {"epic", "gog"}
def get_dlc_flags(store: str) -> list[str]:
    """Get dlc flags."""
    if store.lower() in _DLC_SUPPORTED_STORES:
        return ["--with-dlcs"]
    return []
def store_supports_dlc(store: str) -> bool:
    """Store supports dlc."""
    return store.lower() in _DLC_SUPPORTED_STORES