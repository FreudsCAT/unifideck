"""
DLC Management Module

Centralizes DLC-related logic for all store connectors.
DLCs are automatically downloaded with game installations — no user prompts.

Supported stores:
- Epic Games (legendary): --with-dlcs on install/update
- GOG (gogdl): --with-dlcs on download/repair
- Amazon Games (nile): No DLC support
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Stores that support automatic DLC download
_DLC_SUPPORTED_STORES = {"epic", "gog"}


def get_dlc_flags(store: str) -> List[str]:
    """Return CLI flags to include DLCs for a given store.
    
    These flags should be appended to install/update/repair commands.
    
    Args:
        store: Store identifier ('epic', 'gog', 'amazon')
        
    Returns:
        List of CLI flag strings, e.g. ['--with-dlcs']
    """
    if store.lower() in _DLC_SUPPORTED_STORES:
        return ["--with-dlcs"]
    return []


def store_supports_dlc(store: str) -> bool:
    """Check if a store supports automatic DLC download.
    
    Args:
        store: Store identifier ('epic', 'gog', 'amazon')
        
    Returns:
        True if the store's CLI tool supports DLC flags
    """
    return store.lower() in _DLC_SUPPORTED_STORES
