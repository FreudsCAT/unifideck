"""
Ubisoft Connect API Client -- Legacy Token Management

Handles loading/clearing the legacy API token file (ubisoft_token.json).
Authentication is now handled natively via UPC (Ubisoft Connect app)
launched in a Wine prefix. This module is retained only for:
  - Reading userId from legacy token files (used in settings.yml injection)
  - Clearing token files on logout
"""
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Token storage path
DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
TOKEN_FILE = os.path.join(DATA_DIR, "ubisoft_token.json")


class UbisoftAPIClient:
    """
    Legacy Ubisoft token file manager.

    Reads userId from the token file (written by previous API-based auth
    sessions) and clears it on logout. No API calls are made.
    """

    def __init__(self):
        self.tokens: Optional[Dict[str, Any]] = None
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load saved tokens from disk."""
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "r") as f:
                    self.tokens = json.load(f)
                logger.info("[Ubisoft API] Loaded saved tokens")
            else:
                self.tokens = None
        except Exception as e:
            logger.warning(f"[Ubisoft API] Failed to load tokens: {e}")
            self.tokens = None

    def _clear_tokens(self) -> None:
        """Remove tokens from memory and disk."""
        self.tokens = None
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
            logger.info("[Ubisoft API] Tokens cleared")
        except Exception as e:
            logger.warning(f"[Ubisoft API] Failed to delete token file: {e}")

    def logout(self) -> Dict[str, Any]:
        """Clear all auth state (tokens from memory and disk)."""
        self._clear_tokens()
        logger.info("[Ubisoft API] Logged out")
        return {"success": True}

    def get_user_id(self) -> Optional[str]:
        """Get the user ID from the legacy token file."""
        if self.tokens:
            return self.tokens.get("userId")
        return None
