"""
Centralized update checking service for Unifideck.

Handles checking for game updates across Epic and GOG,
including store-specific cooldowns (TTL) to prevent API spam.
"""

import time
import asyncio
from typing import Dict, Any, List, Optional
import decky

logger = decky.logger

class UpdateChecker:
    def __init__(self, plugin):
        """
        Initialize the update checker.
        
        Args:
            plugin: Reference to the Plugin containing Epic/GOG/Amazon connectors
        """
        self.plugin = plugin
        self._update_cache: Dict[str, Any] = {}
        # Store individual timestamps for each platform to prevent TTL collisions
        self._store_timestamps: Dict[str, float] = {}
        # 5 minute cache to prevent spamming APIs when rapidly navigating pages
        self._UPDATE_CACHE_TTL = 300

    def _get_store_connector(self, store_id: str):
        if store_id == 'epic':
            return self.plugin.epic
        elif store_id == 'gog':
            return self.plugin.gog
        elif store_id == 'amazon':
            return getattr(self.plugin, 'amazon', None)
        return None

    async def check_for_game_update(self, store: str, game_id: str) -> bool:
        """
        Check if a specific game has an update available using cached batch data when possible.
        """
        if not store or not game_id:
            return False

        has_update = False

        if store == 'epic':
            epic_connector = self._get_store_connector('epic')
            if not epic_connector:
                return False
                
            # Epic uses a batch check - cache all results
            if time.time() - self._store_timestamps.get('epic', 0) > self._UPDATE_CACHE_TTL:
                
                logger.info("[UpdateChecker] Fetching fresh Epic updates using Legendary")
                updates = await epic_connector.check_for_updates()
                
                # Clear out old epic cache entries
                keys_to_delete = [k for k in self._update_cache.keys() if k.startswith('epic:')]
                for k in keys_to_delete:
                    del self._update_cache[k]
                    
                # Add new true results
                for uid in updates:
                    self._update_cache[f'epic:{uid}'] = True
                    
                self._store_timestamps['epic'] = time.time()

            # If it's not in the cache, but timestamp is fresh, we know it's False
            has_update = self._update_cache.get(f'epic:{game_id}', False)

        elif store == 'gog':
            gog_connector = self._get_store_connector('gog')
            if not gog_connector:
                return False
                
            # GOG checks per-game via Content System API
            cache_key = f'gog:{game_id}'
            if (cache_key not in self._update_cache or
                time.time() - self._store_timestamps.get(f'gog_game_{game_id}', 0) > self._UPDATE_CACHE_TTL):
                
                logger.info(f"[UpdateChecker] Fetching fresh GOG update for {game_id}")
                result = await gog_connector.check_for_game_update(game_id)
                self._update_cache[cache_key] = result
                self._store_timestamps[f'gog_game_{game_id}'] = time.time()

            has_update = self._update_cache.get(cache_key, False)

        elif store == 'amazon':
            amazon_connector = self._get_store_connector('amazon')
            if not amazon_connector:
                return False
                
            # Amazon uses batch check — cache all results
            if time.time() - self._store_timestamps.get('amazon', 0) > self._UPDATE_CACHE_TTL:
                
                logger.info("[UpdateChecker] Fetching fresh Amazon updates")
                updates = await amazon_connector.check_for_updates()
                
                # Clear out old amazon cache entries
                keys_to_delete = [k for k in self._update_cache.keys() if k.startswith('amazon:')]
                for k in keys_to_delete:
                    del self._update_cache[k]
                    
                for uid in updates:
                    self._update_cache[f'amazon:{uid}'] = True
                    
                self._store_timestamps['amazon'] = time.time()

            has_update = self._update_cache.get(f'amazon:{game_id}', False)

        return has_update

    def get_cached_update_status(self, store: str, game_id: str) -> Optional[bool]:
        """Returns the currently cached update status without waiting for a re-fetch."""
        cache_key = f'{store}:{game_id}'
        
        # Consider the cache "missed" (None) if the timestamp is completely missing,
        # but don't strictly enforce TTL for get_game_info immediate rendering
        if store in ['epic', 'amazon']:
            if self._store_timestamps.get(store, 0) == 0:
                return None
        elif store == 'gog':
            if self._store_timestamps.get(f'gog_game_{game_id}', 0) == 0:
                return None
                
        return self._update_cache.get(cache_key, False)

    def clear_cache_for_game(self, store: str, game_id: str):
        """Clear the update cache entry for a specific game (e.g. after updating)
        
        Deliberately does not wipe the entire batch _store_timestamps so that
        subsequent checks for OTHER games do not immediately spam the API.
        """
        cache_key = f'{store}:{game_id}'
        # By setting the specific game to False, the frontend immediately sees 'Play'
        # instead of flashing 'Update' when finishing an install/update.
        self._update_cache[cache_key] = False
