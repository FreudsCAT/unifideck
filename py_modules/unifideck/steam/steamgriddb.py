"""
SteamGridDB API Client for fetching game cover art

Requires: pip install python-steamgriddb aiohttp aiofiles
"""

import os
import re
import json
import logging
import asyncio
import unicodedata
import aiohttp
from typing import Optional, List, Dict, Any
from pathlib import Path

# Import asset classes for constructing objects from raw API responses
from steamgrid.asset import Grid as SGDBGrid, Hero as SGDBHero, Logo as SGDBLogo, Icon as SGDBIcon

# Import Steam user detection utility
from py_modules.unifideck.steam.steam_utils import get_logged_in_steam_user

logger = logging.getLogger(__name__)

STEAMGRIDDB_METADATA_TIMEOUT = 15
STEAMGRIDDB_SEARCH_TIMEOUT = 20

try:
    from steamgrid import SteamGridDB
    STEAMGRIDDB_AVAILABLE = True
except ImportError:
    STEAMGRIDDB_AVAILABLE = False
    logger.warning("python-steamgriddb not installed. Cover art features disabled.")


class SteamGridDBClient:
    """Client for fetching game artwork from SteamGridDB"""

    def __init__(self, api_key: Optional[str] = None, steam_path: Optional[str] = None):
        self.api_key = api_key
        self.steam_path = steam_path or self._find_steam_path()
        self.grid_path = self._find_grid_path()

        if not STEAMGRIDDB_AVAILABLE:
            logger.error("SteamGridDB client unavailable - python-steamgriddb not installed")
            self.client = None
        elif not api_key:
            logger.warning("No SteamGridDB API key provided")
            self.client = None
        else:
            try:
                self.client = SteamGridDB(api_key)
                logger.info("SteamGridDB client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize SteamGridDB client: {e}")
                self.client = None

    def _find_steam_path(self) -> Optional[str]:
        """Find Steam installation directory"""
        possible_paths = [
            os.path.expanduser("~/.steam/steam"),
            os.path.expanduser("~/.local/share/Steam"),
        ]

        for path in possible_paths:
            if os.path.exists(os.path.join(path, "steamapps")):
                return path

        return None

    def _find_grid_path(self) -> Optional[str]:
        """Find Steam grid images directory for the logged-in user.
        
        Uses loginusers.vdf to find the user with MostRecent=1, falling
        back to mtime-based detection while explicitly excluding user 0.
        """
        if not self.steam_path:
            return None

        # Use the robust user detection utility
        active_user = get_logged_in_steam_user(self.steam_path)
        
        if not active_user:
            logger.error("[SteamGridDB] Could not determine logged-in Steam user for grid path")
            return None
        
        # Safety check: never use user 0
        if active_user == '0':
            logger.error("[SteamGridDB] User 0 detected - this is a meta-directory, not a real user!")
            return None

        grid_path = os.path.join(self.steam_path, "userdata", active_user, "config", "grid")
        os.makedirs(grid_path, exist_ok=True)

        logger.info(f"[SteamGridDB] Using grid path for user {active_user}: {grid_path}")

        return grid_path

    @staticmethod
    def _normalize_for_sgdb_match(title: str) -> str:
        """Normalize title for SGDB comparison: lowercase, strip symbols, & -> and."""
        if not title:
            return ''
        t = title.lower().strip()
        t = re.sub(r'[\u00ae\u2122\u00a9]', '', t)           # Strip ®™© BEFORE NFKD (NFKD expands ™→TM)
        # Decompose diacritics: ü→u, ñ→n, é→e, etc.
        t = unicodedata.normalize('NFKD', t)
        t = ''.join(c for c in t if not unicodedata.combining(c))
        t = re.sub(r'\((?:tm|r|c)\)', '', t, flags=re.IGNORECASE)  # Strip (TM)(R)(C)
        t = t.replace('&', ' and ')                            # & -> and
        t = t.replace('\u2018', "'").replace('\u2019', "'")   # Smart quotes -> ASCII
        t = t.replace('\u201c', '"').replace('\u201d', '"')
        t = t.replace('_', ' ')                                # Underscores -> spaces
        t = t.replace('-', ' ')                                # Hyphens -> spaces (preserve word boundaries)
        t = t.replace('|', '')                                 # Strip pipe (X|S -> XS, not X S)
        t = re.sub(r'[^\w\s]', ' ', t)                         # Replace remaining punctuation with spaces (preserve word boundaries)
        t = re.sub(r'\s+', ' ', t).strip()                     # Collapse whitespace
        return t

    @staticmethod
    def _strip_edition_suffix(normalized_title: str) -> str:
        """Strip edition/variant/platform suffixes from a normalized title.

        Iteratively strips suffixes so compound cases work:
        'call of duty black ops 6 standard edition windows'
         → strip 'windows' → 'call of duty black ops 6 standard edition'
         → strip 'standard edition' → 'call of duty black ops 6'
        """
        edition_suffixes = [
            # Platform/console suffixes (longer first for most-specific match)
            'xbox series xs edition', 'xbox one edition', 'xbox edition',
            'xbox series xs', 'xbox one',
            'pc edition', 'windows 10 edition', 'windows edition',
            'console edition',
            'for pc', 'for windows', 'for xbox',
            # Distribution/bundle suffixes
            'cross gen bundle', 'game preview',
            'the complete season', 'the complete first season',
            # Full edition names
            'deluxe edition', 'gold edition', 'ultimate edition', 'complete edition',
            'goty edition', 'game of the year edition', 'definitive edition',
            'enhanced edition', 'special edition', 'anniversary edition',
            'premium edition', 'standard edition', 'legacy edition',
            'collectors edition', 'limited edition', 'digital edition',
            'classic edition', 'royal edition', 'legendary edition',
            'remastered', 'remake', 'directors cut', 'the final cut',
            # EA/publisher edition variants
            'revolution',
            # Short/standalone (word boundary ensured by space-prefix check)
            'goty', 'hd', 'ce', 'windows',
        ]
        # Iterative: strip one suffix per pass, repeat until stable
        changed = True
        while changed:
            changed = False
            for suffix in edition_suffixes:
                if normalized_title.endswith(' ' + suffix):
                    stripped = normalized_title[:-(len(suffix) + 1)].strip()
                    if stripped:
                        normalized_title = stripped
                        changed = True
                        break  # Restart suffix scan from the top
        return normalized_title

    @staticmethod
    def _score_sgdb_match(search_norm: str, result_norm: str) -> float:
        """Score match quality between normalized titles (0.0-1.0).

        Uses word-set overlap (Jaccard-like) to detect franchise confusion:
        - 'assassins creed' vs 'assassins creed odyssey' = 0.67 (rejected at 0.85)
        - 'far cry 3' vs 'far cry 3 blood dragon' = 0.60 (rejected)
        """
        if not search_norm or not result_norm:
            return 0.0
        if search_norm == result_norm:
            return 1.0
        search_words = set(search_norm.split())
        result_words = set(result_norm.split())
        if search_words == result_words:
            return 0.95  # Same words, different order
        intersection = search_words & result_words
        union = search_words | result_words
        jaccard = len(intersection) / len(union) if union else 0.0
        return jaccard

    async def search_game(self, title: str) -> Optional[int]:
        """Search for game by title and return SGDB game ID.

        Uses normalized comparison to handle symbol/punctuation differences
        and strict matching to prevent franchise confusion (e.g. 'Assassin's
        Creed' matching 'Assassin's Creed Odyssey').
        """
        if not self.client:
            return None

        try:
            loop = asyncio.get_running_loop()
            # Normalize query sent to SGDB API — strip ®™ and platform/edition tags
            # that may hurt search quality (e.g. "- CE", "(Xbox One)", ": Xbox One Edition")
            clean_query = re.sub(r'[\u00ae\u2122\u00a9]', '', title).strip()
            clean_query = re.sub(r'\s*[-\u2013:]\s*(?:CE|SE|DE|GE)\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s*\((?:Xbox (?:One|Series X\|?S)|PC|Windows|PS[45]|Nintendo Switch|Game Preview)\)\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s*[-\u2013:]\s*Xbox (?:One|Series X\|?S)(?:\s+Edition)?\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s+for\s+Xbox\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s+Xbox\s+(?:One|Series\s+X\|?S)(?:\s+Edition)?\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s*[-\u2013:]\s*(?:Cross[- ]Gen\s+Bundle|The\s+Complete(?:\s+First)?\s+Season)\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            clean_query = re.sub(r'\s*[-\u2013:]\s*(?:Standard|Console)\s+Edition(?:\s*\(Windows\))?\s*$', '', clean_query, flags=re.IGNORECASE).strip()
            results = await asyncio.wait_for(
                loop.run_in_executor(None, self.client.search_game, clean_query),
                timeout=STEAMGRIDDB_SEARCH_TIMEOUT,
            )

            if not results or len(results) == 0:
                return None

            search_norm = self._normalize_for_sgdb_match(title)

            # Pass 1: Normalized exact match (handles ®™©, &/and, punctuation)
            for result in results:
                result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                if result_norm == search_norm:
                    logger.debug(f"Found SGDB ID {result.id} for '{title}' (exact match)")
                    return result.id

            # Pass 2: Edition-variant match (strips edition suffixes, then compares)
            search_base = self._strip_edition_suffix(search_norm)
            for result in results:
                result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                result_base = self._strip_edition_suffix(result_norm)
                if result_base == search_base:
                    logger.debug(f"Found SGDB ID {result.id} for '{title}' (edition-variant match)")
                    return result.id

            # Pass 3: Scored match — pick best candidate above confidence threshold.
            # Threshold 0.85 prevents franchise confusion:
            #   "assassins creed" vs "assassins creed odyssey" = 0.67 -> rejected
            #   "splinter cell" vs "splinter cell blacklist" = 0.80 -> rejected
            best_score = 0.0
            best_result = None
            for result in results:
                result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                result_base = self._strip_edition_suffix(result_norm)
                # Score both full and stripped versions — prevents platform/edition
                # suffixes from tanking Jaccard (e.g. "gta v xbox one" vs "gta v")
                score = max(
                    self._score_sgdb_match(search_norm, result_norm),
                    self._score_sgdb_match(search_base, result_base),
                )
                if score > best_score:
                    best_score = score
                    best_result = result

            if best_score >= 0.85 and best_result:
                logger.debug(f"Found SGDB ID {best_result.id} for '{title}' (scored match: {best_score:.2f})")
                return best_result.id

            # Pass 4: Retry with stripped base title as search query
            # Handles cases where suffixes in the query pollute SGDB search results
            # (e.g., "EA SPORTS FC 25 Xbox Series X|S" → search "ea sports fc 25" instead)
            if search_base != search_norm:
                logger.debug(f"Retrying SGDB search with base title: '{search_base}'")
                retry_results = await asyncio.wait_for(
                    loop.run_in_executor(None, self.client.search_game, search_base),
                    timeout=STEAMGRIDDB_SEARCH_TIMEOUT,
                )
                if retry_results:
                    for result in retry_results:
                        result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                        result_base = self._strip_edition_suffix(result_norm)
                        if result_base == search_base:
                            logger.debug(f"Found SGDB ID {result.id} for '{title}' (retry base match)")
                            return result.id
                    # Scored match on retry results
                    for result in retry_results:
                        result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                        result_base = self._strip_edition_suffix(result_norm)
                        score = max(
                            self._score_sgdb_match(search_norm, result_norm),
                            self._score_sgdb_match(search_base, result_base),
                        )
                        if score >= 0.85:
                            logger.debug(f"Found SGDB ID {result.id} for '{title}' (retry scored: {score:.2f})")
                            return result.id

            # Pass 5: Try without known publisher prefixes
            # SGDB may index games without publisher branding (e.g., "College Football 25" not "EA SPORTS College Football 25")
            publisher_prefixes = ['ea sports', 'tom clancys', 'sid meiers']
            for prefix in publisher_prefixes:
                if search_base.startswith(prefix + ' '):
                    short_title = search_base[len(prefix):].strip()
                    logger.debug(f"Retrying SGDB search without publisher prefix: '{short_title}'")
                    prefix_results = await asyncio.wait_for(
                        loop.run_in_executor(None, self.client.search_game, short_title),
                        timeout=STEAMGRIDDB_SEARCH_TIMEOUT,
                    )
                    if prefix_results:
                        for result in prefix_results:
                            result_norm = self._normalize_for_sgdb_match(getattr(result, 'name', ''))
                            result_base = self._strip_edition_suffix(result_norm)
                            if result_base == short_title or result_base == search_base:
                                logger.debug(f"Found SGDB ID {result.id} for '{title}' (prefix-stripped match)")
                                return result.id
                    break  # Only try one prefix

            # No confident match — return None so pipeline falls through to Steam CDN
            logger.debug(f"No confident SGDB match for '{title}' (best score: {best_score:.2f})")
            return None

        except asyncio.TimeoutError:
            logger.warning(f"SteamGridDB search timed out for '{title}'")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error searching for game '{title}': {e}")

        return None

    # Style preference order for artwork selection.
    # Alternate is preferred (high-quality game-specific covers), white_logo last.
    # NOTE: SGDB API v2 returns 0 for score/upvotes/downvotes, so the API's
    # default ordering (which reflects server-side popularity) is our primary
    # quality signal.  Python's sort is stable, so within each style group the
    # original API order is preserved.
    _STYLE_PRIORITY = {
        'alternate': 0,   # Preferred: game-specific cover art
        'blurred': 1,
        'material': 1,
        'no_logo': 1,
        'white_logo': 2,  # Least preferred
    }

    def select_best_artwork(self, assets: List) -> Optional[Any]:
        """
        Select the best artwork from a list of assets.

        The SGDB API v2 returns 0 for score/upvotes/downvotes, so the API's
        default ordering (server-side popularity) is our best quality signal
        after style and resolution.

        Priority:
        1. Official/locked images (asset._lock == True)
        2. Style preference (alternate > blurred/material/no_logo > white_logo)
        3. Highest score (when/if API returns non-zero values)
        4. Highest resolution (important for heroes/logos quality)
        5. API position (popularity tiebreaker for same-resolution results)
        """
        if not assets:
            return None

        # Filter out NSFW/humor if desired
        filtered = [a for a in assets if not getattr(a, '_nsfw', False) and not getattr(a, '_humor', False)]
        if not filtered:
            filtered = assets  # Fall back to all if filtering removed everything

        # Sort by priority — enumerate captures API position as tiebreaker
        sorted_pairs = sorted(
            enumerate(filtered),
            key=lambda pair: (
                not getattr(pair[1], '_lock', False),     # Official/locked first
                self._STYLE_PRIORITY.get(getattr(pair[1], 'style', ''), 1),  # Style preference
                -(getattr(pair[1], 'score', 0) or 0),     # Highest score (future-proof)
                -((getattr(pair[1], 'width', 0) or 0) * (getattr(pair[1], 'height', 0) or 0)),  # Higher res preferred
                pair[0],  # API position: popularity tiebreaker
            )
        )

        return sorted_pairs[0][1]

    def _fetch_grids_by_dimensions(self, sgdb_game_id: int, dimensions: str = None, styles: str = 'alternate,white_logo,no_logo,blurred,material') -> Optional[List]:
        """Fetch grids from SGDB, optionally filtered by dimensions and styles.

        Uses the HTTP client directly to pass query parameters
        which the vendored steamgrid library doesn't expose.
        When dimensions/styles are None (fallback mode), those filters are omitted.
        """
        queries = {
            'nsfw': 'false',
            'humor': 'false',
        }
        if dimensions:
            queries['dimensions'] = dimensions
        if styles:
            queries['styles'] = styles
        try:
            payloads = self.client._http.get_grid([sgdb_game_id], 'game', queries=queries)
            if payloads:
                return [SGDBGrid(p, self.client._http) for p in payloads]
        except Exception as e:
            logger.debug(f"SGDB grid fetch (dims={dimensions}) failed: {e}")
        return None

    def _fetch_heroes(self, sgdb_game_id: int, dimensions: str = '1920x620,3840x1240', styles: str = 'alternate,blurred,material') -> Optional[List]:
        """Fetch heroes from SGDB with optional dimension and style filtering."""
        queries = {
            'nsfw': 'false',
            'humor': 'false',
        }
        if dimensions:
            queries['dimensions'] = dimensions
        if styles:
            queries['styles'] = styles
        try:
            payloads = self.client._http.get_hero([sgdb_game_id], 'game', queries=queries)
            if payloads:
                return [SGDBHero(p, self.client._http) for p in payloads]
        except Exception as e:
            logger.debug(f"SGDB hero fetch failed: {e}")
        return None

    def _fetch_logos(self, sgdb_game_id: int, styles: str = 'official,white,black,custom') -> Optional[List]:
        """Fetch logos from SGDB with optional style filtering."""
        queries = {
            'nsfw': 'false',
            'humor': 'false',
        }
        if styles:
            queries['styles'] = styles
        try:
            payloads = self.client._http.get_logo([sgdb_game_id], 'game', queries=queries)
            if payloads:
                return [SGDBLogo(p, self.client._http) for p in payloads]
        except Exception as e:
            logger.debug(f"SGDB logo fetch failed: {e}")
        return None

    def _fetch_icons(self, sgdb_game_id: int) -> Optional[List]:
        """Fetch icons from SGDB with filtering."""
        queries = {
            'nsfw': 'false',
            'humor': 'false',
        }
        try:
            payloads = self.client._http.get_icon([sgdb_game_id], 'game', queries=queries)
            if payloads:
                return [SGDBIcon(p, self.client._http) for p in payloads]
        except Exception as e:
            logger.debug(f"SGDB icon fetch failed: {e}")
        return None

    async def download_image(self, url: str, save_path: str, timeout: int = 30) -> bool:
        """Download image from URL to local path

        Args:
            url: URL to download from
            save_path: Local path to save the image
            timeout: Timeout in seconds for the download (default 30s)
        """
        tmp_path = f"{save_path}.tmp"
        try:
            # Temporarily disable SSL verification to work around certificate validation issues
            # TODO: Fix properly by updating system CA certificates or certifi package
            connector = aiohttp.TCPConnector(ssl=False)
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.read()
                        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(tmp_path, 'wb') as f:
                            f.write(content)
                        os.replace(tmp_path, save_path)
                        logger.info(f"Downloaded image to {save_path}")
                        return True
                    else:
                        logger.error(f"Failed to download image: HTTP {response.status}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout downloading image from {url}")
            return False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error downloading image: {e}")
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

        return False

    async def get_grid_images(self, sgdb_game_id: int, app_id: int, 
                               only_types: set = None) -> Dict[str, bool]:
        """
        Fetch and download grid images for a game - ALL IN PARALLEL
        Returns dict of image type -> success status
        
        Args:
            only_types: If specified, only download these art types (e.g., {'logo', 'icon'}).
                       This prevents overwriting existing artwork from other sources.
        """
        if not self.client or not self.grid_path:
            return {}

        # Convert signed int32 to unsigned for Steam filename compatibility
        unsigned_id = app_id if app_id >= 0 else app_id + 2**32

        results = {'grid': False, 'grid_l': False, 'hero': False, 'logo': False, 'icon': False}

        # Default to all types if not specified
        if only_types is None:
            only_types = {'grid', 'grid_l', 'hero', 'logo', 'icon'}

        try:
            loop = asyncio.get_running_loop()

            # PHASE 1: Fetch ALL artwork metadata from SGDB API in PARALLEL
            # Use dimension-filtered calls for grids to get proper portrait/landscape results
            # (without filtering, the 50-result page limit causes landscape grids to be crowded out)
            need_portrait = 'grid' in only_types
            need_landscape = 'grid_l' in only_types

            api_tasks = []
            task_labels = []

            if need_portrait:
                api_tasks.append(loop.run_in_executor(
                    None, self._fetch_grids_by_dimensions, sgdb_game_id, '600x900'))
                task_labels.append('portrait_grids')
            if need_landscape:
                api_tasks.append(loop.run_in_executor(
                    None, self._fetch_grids_by_dimensions, sgdb_game_id, '920x430,460x215'))
                task_labels.append('landscape_grids')

            api_tasks.append(loop.run_in_executor(None, self._fetch_heroes, sgdb_game_id))
            task_labels.append('heroes')
            api_tasks.append(loop.run_in_executor(None, self._fetch_logos, sgdb_game_id))
            task_labels.append('logos')
            api_tasks.append(loop.run_in_executor(None, self._fetch_icons, sgdb_game_id))
            task_labels.append('icons')

            api_results = await asyncio.wait_for(
                asyncio.gather(*api_tasks, return_exceptions=True),
                timeout=STEAMGRIDDB_SEARCH_TIMEOUT,
            )

            # Map results back to named variables
            fetched = dict(zip(task_labels, api_results))
            portrait_grids = fetched.get('portrait_grids')
            landscape_grids = fetched.get('landscape_grids')
            heroes = fetched.get('heroes')
            logos = fetched.get('logos')
            icons = fetched.get('icons')

            # PHASE 1.5: Fallback with relaxed filters for types that got no results
            # If primary filters (Steam dimensions, preferred styles) returned nothing,
            # retry with broader dimensions (incl. Galaxy 2.0) and no style restrictions
            fallback_tasks = []
            fallback_labels = []

            if need_portrait and (not portrait_grids or isinstance(portrait_grids, Exception)):
                fallback_tasks.append(loop.run_in_executor(
                    None, self._fetch_grids_by_dimensions, sgdb_game_id, '600x900,660x930,342x482', None))
                fallback_labels.append('portrait_grids')
            if 'hero' in only_types and (not heroes or isinstance(heroes, Exception)):
                fallback_tasks.append(loop.run_in_executor(
                    None, self._fetch_heroes, sgdb_game_id, None, None))
                fallback_labels.append('heroes')
            if 'logo' in only_types and (not logos or isinstance(logos, Exception)):
                fallback_tasks.append(loop.run_in_executor(
                    None, self._fetch_logos, sgdb_game_id, None))
                fallback_labels.append('logos')

            if fallback_tasks:
                logger.debug(f"SGDB fallback fetch (relaxed filters) for: {fallback_labels}")
                fallback_results = await asyncio.wait_for(
                    asyncio.gather(*fallback_tasks, return_exceptions=True),
                    timeout=STEAMGRIDDB_SEARCH_TIMEOUT,
                )
                fallback_fetched = dict(zip(fallback_labels, fallback_results))
                if 'portrait_grids' in fallback_fetched and not isinstance(fallback_fetched['portrait_grids'], Exception):
                    portrait_grids = fallback_fetched['portrait_grids']
                if 'heroes' in fallback_fetched and not isinstance(fallback_fetched['heroes'], Exception):
                    heroes = fallback_fetched['heroes']
                if 'logos' in fallback_fetched and not isinstance(fallback_fetched['logos'], Exception):
                    logos = fallback_fetched['logos']

            # PHASE 2: Select best artwork and prepare downloads (only for requested types)
            download_tasks = []
            task_types = []

            # Portrait grid - only if requested
            if need_portrait and portrait_grids and not isinstance(portrait_grids, Exception):
                best_grid = self.select_best_artwork(portrait_grids)
                if best_grid:
                    grid_file = os.path.join(self.grid_path, f"{unsigned_id}p.jpg")
                    download_tasks.append(self.download_image(best_grid.url, grid_file))
                    task_types.append('grid')

            # Landscape grid - only if requested
            if need_landscape and landscape_grids and not isinstance(landscape_grids, Exception):
                best_landscape = self.select_best_artwork(landscape_grids)
                if best_landscape:
                    landscape_file = os.path.join(self.grid_path, f"{unsigned_id}.jpg")
                    download_tasks.append(self.download_image(best_landscape.url, landscape_file))
                    task_types.append('grid_l')
            
            # Hero - only if requested
            if 'hero' in only_types and heroes and not isinstance(heroes, Exception):
                best_hero = self.select_best_artwork(heroes)
                if best_hero:
                    hero_file = os.path.join(self.grid_path, f"{unsigned_id}_hero.jpg")
                    download_tasks.append(self.download_image(best_hero.url, hero_file))
                    task_types.append('hero')
            
            # Logo - only if requested
            if 'logo' in only_types and logos and not isinstance(logos, Exception):
                best_logo = self.select_best_artwork(logos)
                if best_logo:
                    logo_file = os.path.join(self.grid_path, f"{unsigned_id}_logo.png")
                    download_tasks.append(self.download_image(best_logo.url, logo_file))
                    task_types.append('logo')
            
            # Icon - only if requested
            if 'icon' in only_types and icons and not isinstance(icons, Exception):
                best_icon = self.select_best_artwork(icons)
                if best_icon:
                    icon_file = os.path.join(self.grid_path, f"{unsigned_id}_icon.jpg")
                    download_tasks.append(self.download_image(best_icon.url, icon_file))
                    task_types.append('icon')
            
            # PHASE 3: Download ALL images in PARALLEL
            if download_tasks:
                download_results = await asyncio.gather(*download_tasks, return_exceptions=True)
                
                for i, result in enumerate(download_results):
                    if result is True and task_types[i] in results:
                        results[task_types[i]] = True

        except asyncio.TimeoutError:
            logger.warning(f"Timed out fetching SGDB image metadata for game {sgdb_game_id}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error fetching grid images: {e}")

        return results

    async def get_steam_metadata(self, title: str) -> Dict[str, Any]:
        """
        Fetch Steam metadata (AppID and CDN URLs)
        Returns: {'steam_id': id, 'urls': {type: url}}
        """
        result = {'steam_id': None, 'urls': {}}
        
        try:
            steam_app_id = await self.search_steam_appid(title)
            if not steam_app_id:
                return result
                
            result['steam_id'] = steam_app_id
            
            # CDN URLs
            result['urls'] = {
                'grid': f"https://shared.steamstatic.com/store_item_assets/steam/apps/{steam_app_id}/library_600x900_2x.jpg",
                'grid_l': f"https://shared.steamstatic.com/store_item_assets/steam/apps/{steam_app_id}/header.jpg",
                'hero': f"https://shared.steamstatic.com/store_item_assets/steam/apps/{steam_app_id}/library_hero.jpg",
                'logo': f"https://shared.steamstatic.com/store_item_assets/steam/apps/{steam_app_id}/logo.png"
            }
            
        except Exception as e:
            logger.debug(f"Steam metadata error for '{title}': {e}")
            
        return result

    async def get_gog_metadata(self, gog_product_id: int) -> Dict[str, Any]:
        """Fetch GOG artwork URLs from Galaxy GamesDB API (includes vertical_cover)"""
        result = {'urls': {}}
        
        logger.info(f"[GOG Artwork] Fetching metadata for product ID: {gog_product_id}")
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            client_timeout = aiohttp.ClientTimeout(total=STEAMGRIDDB_METADATA_TIMEOUT)
            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
                # Use Galaxy GamesDB API which provides vertical_cover (box art)
                gamesdb_url = f"https://gamesdb.gog.com/platforms/gog/external_releases/{gog_product_id}"
                
                logger.info(f"[GOG Artwork] GamesDB URL: {gamesdb_url}")
                
                async with session.get(gamesdb_url) as response:
                    logger.info(f"[GOG Artwork] GamesDB response status: {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        game = data.get('game', {})
                        
                        if not game:
                            logger.warning(f"[GOG Artwork] GamesDB returned empty game object for {gog_product_id} - product may not exist in database")
                        else:
                            logger.info(f"[GOG Artwork] GamesDB game data keys: {list(game.keys())}")
                        
                        # Grid: Vertical cover (box art) - THIS IS WHAT WE NEED!
                        vertical_cover = game.get('vertical_cover', {})
                        if vertical_cover.get('url_format'):
                            url = vertical_cover['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['grid'] = url
                            logger.info(f"[GOG Artwork]   ✓ Found grid: {url[:80]}...")
                        
                        # Hero: Background image
                        background = game.get('background', {})
                        if background.get('url_format'):
                            url = background['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['hero'] = url
                            logger.info(f"[GOG Artwork]   ✓ Found hero: {url[:80]}...")
                        
                        # Logo
                        logo = game.get('logo', {})
                        if logo.get('url_format'):
                            url = logo['url_format'].replace('{formatter}', '').replace('{ext}', 'png')
                            result['urls']['logo'] = url
                            logger.info(f"[GOG Artwork]   ✓ Found logo: {url[:80]}...")
                        
                        # Icon (square_icon preferred, fallback to icon)
                        icon = game.get('square_icon', {}) or game.get('icon', {})
                        if icon.get('url_format'):
                            url = icon['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['icon'] = url
                            logger.info(f"[GOG Artwork]   ✓ Found icon: {url[:80]}...")
                    
                    elif response.status == 404:
                        logger.warning(f"[GOG Artwork] Product {gog_product_id} not found in GamesDB (404) - falling back to GOG API")
                    elif response.status >= 400:
                        logger.warning(f"[GOG Artwork] GamesDB error {response.status} for {gog_product_id} - falling back to GOG API")
                    
                    # Fallback to basic products API if GamesDB fails or returns no artwork
                    if not result['urls']:
                        logger.info(f"[GOG Artwork] No URLs from GamesDB, trying fallback GOG products API")
                        api_url = f"https://api.gog.com/products/{gog_product_id}?expand=description"
                        
                        async with session.get(api_url) as prod_response:
                            logger.info(f"[GOG Artwork] GOG API response status: {prod_response.status}")
                            
                            if prod_response.status == 200:
                                data = await prod_response.json()
                                images = data.get('images', {})
                                
                                logger.info(f"[GOG Artwork] Fallback API image keys: {list(images.keys())}")
                                
                                if images.get('icon'):
                                    url = images['icon']
                                    if url.startswith('//'): url = 'https:' + url
                                    result['urls']['icon'] = url
                                    logger.info(f"[GOG Artwork]   ✓ Fallback icon")
                                
                                if images.get('logo2x') or images.get('logo'):
                                    url = images.get('logo2x') or images.get('logo')
                                    if url.startswith('//'): url = 'https:' + url
                                    result['urls']['logo'] = url
                                    logger.info(f"[GOG Artwork]   ✓ Fallback logo")
                                
                                if images.get('background'):
                                    url = images['background']
                                    if url.startswith('//'): url = 'https:' + url
                                    result['urls']['hero'] = url
                                    logger.info(f"[GOG Artwork]   ✓ Fallback hero")
                            elif prod_response.status == 404:
                                logger.warning(f"[GOG Artwork] Product {gog_product_id} not found in GOG API (404) - may be delisted")
                            else:
                                logger.warning(f"[GOG Artwork] GOG API error: {prod_response.status}")
                        
        except Exception as e:
            logger.error(f"[GOG Artwork] Exception fetching metadata for {gog_product_id}: {e}", exc_info=True)
        
        # Final summary
        if result['urls']:
            logger.info(f"[GOG Artwork] Product {gog_product_id}: Found {len(result['urls'])} artwork URLs ({', '.join(result['urls'].keys())})")
        else:
            logger.warning(f"[GOG Artwork] Product {gog_product_id}: No artwork found from any source")
            
        return result

    async def get_epic_metadata(self, epic_app_name: str) -> Dict[str, Any]:
        """Fetch Epic artwork URLs from Legendary cache"""
        result = {'urls': {}}
        
        try:
            legendary_path = Path.home() / ".config" / "legendary" / "metadata"
            meta_file = legendary_path / f"{epic_app_name}.json"
            
            if not meta_file.exists():
                # Try scanning for app_name
                for f in legendary_path.glob("*.json"):
                    try:
                        with open(f) as fp:
                            data = json.load(fp)
                            if data.get('app_name') == epic_app_name:
                                meta_file = f
                                break
                    except: continue
                else:
                    return result
            
            with open(meta_file) as f:
                data = json.load(f)
            
            key_images = data.get('keyImages', []) or data.get('metadata', {}).get('keyImages', [])
            if not key_images:
                return result
                
            # Priority: Prefer vertical covers first for proper box art display
            type_mapping = {
                'grid': ['DieselGameBoxTall', 'OfferImageTall', 'DieselStoreFrontTall', 'DieselGameBox', 'Thumbnail'],
                'hero': ['OfferImageWide', 'DieselGameBoxWide', 'DieselStoreFrontWide', 'featuredMedia'],
                'logo': ['DieselGameBoxLogo', 'ProductLogo'],
            }
            
            for art_type, epic_types in type_mapping.items():
                for et in epic_types:
                    # Find first matching image for this priority type
                    for img in key_images:
                        if img.get('type') == et and img.get('url'):
                            result['urls'][art_type] = img['url']
                            break
                    else: continue
                    break
                    
        except Exception as e:
            logger.debug(f"Epic metadata error: {e}")
            
        return result

    async def get_amazon_metadata(self, amazon_game_id: str) -> Dict[str, Any]:
        """Fetch Amazon artwork URLs from GOG GamesDB API (same approach as Heroic)
        
        Amazon's library.json only has horizontal images (512x288).
        GOG's GamesDB provides vertical_cover with proper dimensions for Steam's grid.
        """
        result = {'urls': {}}
        
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            client_timeout = aiohttp.ClientTimeout(total=STEAMGRIDDB_METADATA_TIMEOUT)
            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
                # Use GOG GamesDB API for Amazon games (same as Heroic!)
                # This provides vertical_cover with proper dimensions
                gamesdb_url = f"https://gamesdb.gog.com/platforms/amazon/external_releases/{amazon_game_id}"
                
                logger.debug(f"[Amazon] Fetching artwork from GamesDB: {gamesdb_url}")
                
                async with session.get(gamesdb_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        game = data.get('game', {})
                        title = data.get('title', {}).get('*', 'Unknown')
                        
                        logger.debug(f"[Amazon] GamesDB found: '{title}'")
                        
                        # Grid: Vertical cover (proper box art!)
                        vertical_cover = game.get('vertical_cover', {})
                        if vertical_cover.get('url_format'):
                            url = vertical_cover['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['grid'] = url
                            logger.debug(f"[Amazon]   grid (GamesDB): {url[:60]}...")
                        
                        # Hero: Background image
                        background = game.get('background', {})
                        if background.get('url_format'):
                            url = background['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['hero'] = url
                        
                        # Logo
                        logo = game.get('logo', {})
                        if logo.get('url_format'):
                            url = logo['url_format'].replace('{formatter}', '').replace('{ext}', 'png')
                            result['urls']['logo'] = url
                        
                        # Icon (square_icon preferred)
                        icon = game.get('square_icon', {}) or game.get('icon', {})
                        if icon.get('url_format'):
                            url = icon['url_format'].replace('{formatter}', '').replace('{ext}', 'jpg')
                            result['urls']['icon'] = url
                    else:
                        logger.debug(f"[Amazon] GamesDB returned {response.status}, falling back to library.json")
                
                # Fallback to Nile library.json for any missing artwork
                if not result['urls'].get('hero') or not result['urls'].get('logo'):
                    nile_library = Path.home() / ".config" / "nile" / "library.json"
                    if nile_library.exists():
                        with open(nile_library) as f:
                            library = json.load(f)
                        
                        for entry in library:
                            product = entry.get('product', {})
                            if product.get('id') == amazon_game_id:
                                detail = product.get('productDetail', {})
                                details = detail.get('details', {})
                                
                                # Hero fallback
                                if not result['urls'].get('hero') and details.get('backgroundUrl1'):
                                    result['urls']['hero'] = details['backgroundUrl1']
                                
                                # Logo fallback
                                if not result['urls'].get('logo') and details.get('logoUrl'):
                                    result['urls']['logo'] = details['logoUrl']
                                
                                # Icon fallback
                                if not result['urls'].get('icon') and detail.get('iconUrl'):
                                    result['urls']['icon'] = detail['iconUrl']
                                
                                break
                        
        except Exception as e:
            logger.debug(f"Amazon GamesDB error: {e}")
            
        return result

    async def search_steam_appid(self, title: str) -> Optional[int]:
        """
        Search Steam Store for AppID by game title.
        Uses title validation to prevent wrong matches (e.g., "Cars" matching "Brave").
        """
        try:
            import urllib.parse
            encoded = urllib.parse.quote(title)
            url = f"https://store.steampowered.com/api/storesearch/?term={encoded}&cc=US"
            
            connector = aiohttp.TCPConnector(ssl=False)
            client_timeout = aiohttp.ClientTimeout(total=STEAMGRIDDB_METADATA_TIMEOUT)
            async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    items = data.get('items', [])
                    
                    # Normalize search title for comparison
                    search_lower = title.lower().strip()
                    
                    for item in items:
                        steam_name = item.get('name', '').lower().strip()
                        steam_id = item.get('id')
                        
                        if not steam_id:
                            continue
                        
                        # Exact match - best case
                        if steam_name == search_lower:
                            logger.debug(f"Steam search: '{title}' -> AppID {steam_id} (exact match)")
                            return steam_id
                        
                        # Check if one contains the other (for editions/subtitles)
                        # "Ghostrunner 2" should match "Ghostrunner 2" not "Ghostrunner"
                        # "Batman Season 2" shouldn't match "Batman Season 1"
                        if search_lower == steam_name:
                            logger.debug(f"Steam search: '{title}' -> AppID {steam_id} (exact match)")
                            return steam_id
                        
                        # Strict containment: the search title should be found as-is
                        # "Disney•Pixar Cars" should only match if Steam has that exact title
                        if steam_name.startswith(search_lower) or search_lower.startswith(steam_name):
                            # Check it's not a different game in the series
                            # E.g., "Ghostrunner" shouldn't match "Ghostrunner 2"
                            # Allow edition suffixes like "GOTY Edition", "Definitive Edition"
                            remainder = steam_name.replace(search_lower, '').strip()
                            remainder2 = search_lower.replace(steam_name, '').strip()
                            
                            # Allow common suffixes
                            allowed_suffixes = ['edition', 'goty', 'definitive', 'ultimate', 'complete', 
                                              'enhanced', 'remastered', 'hd', 'remake']
                            
                            is_safe_suffix = any(suf in remainder.lower() for suf in allowed_suffixes) or \
                                           any(suf in remainder2.lower() for suf in allowed_suffixes) or \
                                           remainder == '' or remainder2 == ''
                            
                            if is_safe_suffix:
                                logger.debug(f"Steam search: '{title}' -> AppID {steam_id} (prefix match)")
                                return steam_id
                    
                    # No good match found
                    logger.debug(f"Steam search: '{title}' -> No validated match in {len(items)} results")
                    return None
                    
        except Exception as e:
            logger.debug(f"Steam search error for '{title}': {e}")
        
        return None

    async def fetch_game_art(self, title: str, app_id: int, store: str = None, store_id: str = None, only_types: set = None, extra: dict = None, sgdb_game_id: int = None) -> Dict[str, Any]:
        """
        Orchestrated Artwork Pipeline:
        1. Metadata Phase: Fetch URLs from all sources CONCURRENTLY
        2. Selection Phase: Prioritize Store URLs > Steam URLs
        3. Download Phase: Download unique, selected images CONCURRENTLY
        
        Args:
            only_types: If provided, only fetch/download these artwork types
                       ('grid', 'grid_l', 'hero', 'logo', 'icon'). If None, attempt all types.
        """
        final_result = {'success': False, 'steam_app_id': None, 'sources': []}

        # Default to all types if not specified
        if only_types is None:
            only_types = {'grid', 'grid_l', 'hero', 'logo', 'icon'}
        
        # Unsigned ID for filenames
        unsigned_id = app_id if app_id >= 0 else app_id + 2**32
        
        try:
            # === PHASE 1: METADATA FETCH (Parallel) ===
            tasks = []
            
            # Always check Steam (for ID + backup art)
            tasks.append(self.get_steam_metadata(title))
            
            # Store-specific checks
            if store == 'gog' and store_id:
                try:
                    gog_id = int(store_id)
                    logger.info(f"[Artwork] Fetching GOG metadata for product ID: {gog_id}")
                    tasks.append(self.get_gog_metadata(gog_id))
                except ValueError as e:
                    logger.error(f"[Artwork] Invalid GOG product ID '{store_id}': {e}")
                    tasks.append(asyncio.sleep(0, result={'urls': {}})) # Dummy
                except Exception as e:
                    logger.error(f"[Artwork] Error preparing GOG metadata fetch for {store_id}: {e}")
                    tasks.append(asyncio.sleep(0, result={'urls': {}})) # Dummy
            elif store == 'epic' and store_id:
                tasks.append(self.get_epic_metadata(store_id))
            elif store == 'amazon' and store_id:
                tasks.append(self.get_amazon_metadata(store_id))
            elif store == 'ubisoft' and extra:
                # Ubisoft provides artwork URLs directly from GraphQL API (stored in game.extra)
                ubisoft_urls = {}
                if extra.get('coverUrl'):
                    ubisoft_urls['grid'] = extra['coverUrl']
                if extra.get('backgroundUrl'):
                    ubisoft_urls['hero'] = extra['backgroundUrl']
                tasks.append(asyncio.sleep(0, result={'urls': ubisoft_urls}))
                logger.info(f"[Artwork] Using Ubisoft API artwork: {list(ubisoft_urls.keys())}")
            else:
                tasks.append(asyncio.sleep(0, result={'urls': {}})) # Dummy to keep parallel structure simple
                
            # Wait for both metadata sources without letting one hung request block the whole game.
            steam_res, store_res = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=STEAMGRIDDB_METADATA_TIMEOUT,
            )
            
            # Save Steam ID if found (for reference, but don't prioritize Steam artwork)
            if steam_res.get('steam_id'):
                final_result['steam_app_id'] = steam_res['steam_id']
                
            # === PHASE 2: SELECTION ===
            # Priority: Store (authoritative) > SGDB (fallback) > Steam CDN (last resort)
            
            # Start with STORE URLs as the authoritative source
            store_urls = store_res.get('urls', {})
            store_label = store.upper() if store else 'STORE'
            
            # Skip artwork types not available or poor quality from official sources:
            # - GOG/Amazon logos from GamesDB are thumbnails, not proper logos
            # - Icons are not available from any store's official metadata
            if store in ('gog', 'amazon'):
                store_urls.pop('logo', None)  # Force from Steam/SGDB instead
                logger.debug(f"[Artwork] Skipping {store.upper()} logo (thumbnail quality)")
            store_urls.pop('icon', None)  # Icons never available from stores, use SGDB
            
            selected_urls = {}
            source_map = {}
            
            # Add remaining store URLs (they are authoritative for this game)
            # Filter to only requested types
            for k, url in store_urls.items():
                if url and k in only_types:
                    selected_urls[k] = url
                    source_map[k] = store_label
            
            logger.debug(f"[Artwork] Store provided: {list(selected_urls.keys())}")
            
            # === PHASE 3: DOWNLOAD (Parallel) ===
            download_tasks = []
            
            # Map art types to filenames
            # Note: We only download the WINNER for each type
            
            if 'grid' in selected_urls:
                path = os.path.join(self.grid_path, f"{unsigned_id}p.jpg")
                task = self.download_image(selected_urls['grid'], path)
                download_tasks.append((task, 'grid'))

            if 'grid_l' in selected_urls:
                path = os.path.join(self.grid_path, f"{unsigned_id}.jpg")
                task = self.download_image(selected_urls['grid_l'], path)
                download_tasks.append((task, 'grid_l'))

            if 'hero' in selected_urls:
                path = os.path.join(self.grid_path, f"{unsigned_id}_hero.jpg")
                task = self.download_image(selected_urls['hero'], path)
                download_tasks.append((task, 'hero'))
                
            if 'logo' in selected_urls:
                path = os.path.join(self.grid_path, f"{unsigned_id}_logo.png")
                task = self.download_image(selected_urls['logo'], path)
                download_tasks.append((task, 'logo'))
                
            if 'icon' in selected_urls:
                # File ext might be png or jpg, force jpg for Steam icon usually? 
                # Actually Steam uses jpg mostly, but let's stick to .jpg for simplicity or respect URL
                # The old code forced _icon.jpg
                path = os.path.join(self.grid_path, f"{unsigned_id}_icon.jpg")
                task = self.download_image(selected_urls['icon'], path)
                download_tasks.append((task, 'icon'))

            # Execute downloads
            downloaded = set()
            if download_tasks:
                d_coroutines = [t[0] for t in download_tasks]
                d_results = await asyncio.gather(*d_coroutines, return_exceptions=True)
                
                for i, res in enumerate(d_results):
                    if res is True:
                        art_type = download_tasks[i][1]
                        downloaded.add(art_type)
                    elif isinstance(res, Exception):
                        logger.debug(f"Download failed for {download_tasks[i][1]} artwork on {title}: {res}")
            
            # Build Source Log
            # e.g. "STEAM:grid+logo GOG:hero+icon"
            summary_parts = []

            by_source = {}
            for k in downloaded:
                src = source_map.get(k, 'UNKNOWN')
                if src not in by_source: by_source[src] = []
                by_source[src].append(k)

            for src, types in by_source.items():
                summary_parts.append(f"{src}:{'+'.join(sorted(types))}")

            final_result['sources'] = summary_parts
            final_result['artwork_count'] = len(downloaded)

            # === PHASE 4: FALLBACK (SGDB) ===
            # If significant art is missing, use SGDB to fill gaps
            # Include icon since no store provides proper icons
            # Respect caller's only_types filter — never download types they didn't request
            needed = {'grid', 'grid_l', 'hero', 'logo', 'icon'}
            missing = (needed - downloaded) & only_types
            
            if missing and self.client:
                try:
                    gid = sgdb_game_id or await self.search_game(title)
                    if gid:
                        sgdb_results = await self.get_grid_images(gid, app_id, only_types=missing)
                        # Track SGDB downloads that succeeded
                        for art_type, success in sgdb_results.items():
                            if success and art_type not in downloaded:
                                downloaded.add(art_type)
                                source_map[art_type] = 'SGDB'
                        final_result['sgdb_filled'] = True
                except Exception as e:
                    logger.debug(f"SGDB fallback failed for {title}: {e}")
            
            # === PHASE 5: STEAM CDN (Last Resort) ===
            # Only use Steam CDN for remaining gaps after Store and SGDB
            still_missing = (needed - downloaded) & only_types
            
            if still_missing and steam_res.get('urls'):
                steam_urls = steam_res.get('urls', {})
                
                # Only fill gaps, don't overwrite existing art
                for art_type in still_missing:
                    if art_type in steam_urls and steam_urls[art_type]:
                        # Download Steam art for this type
                        if art_type == 'grid':
                            path = os.path.join(self.grid_path, f"{unsigned_id}p.jpg")
                            if await self.download_image(steam_urls['grid'], path):
                                downloaded.add('grid')
                                source_map['grid'] = 'STEAM'
                        elif art_type == 'grid_l' and 'grid_l' in steam_urls:
                            path = os.path.join(self.grid_path, f"{unsigned_id}.jpg")
                            if await self.download_image(steam_urls['grid_l'], path):
                                downloaded.add('grid_l')
                                source_map['grid_l'] = 'STEAM'
                        elif art_type == 'hero':
                            path = os.path.join(self.grid_path, f"{unsigned_id}_hero.jpg")
                            if await self.download_image(steam_urls['hero'], path):
                                downloaded.add('hero')
                                source_map['hero'] = 'STEAM'
                        elif art_type == 'logo':
                            path = os.path.join(self.grid_path, f"{unsigned_id}_logo.png")
                            if await self.download_image(steam_urls['logo'], path):
                                downloaded.add('logo')
                                source_map['logo'] = 'STEAM'
                
                logger.debug(f"[Artwork] Steam CDN filled gaps: {still_missing & downloaded}")
            
            # Rebuild final sources summary
            by_source = {}
            for k in downloaded:
                src = source_map.get(k, 'UNKNOWN')
                if src not in by_source: by_source[src] = []
                by_source[src].append(k)

            summary_parts = []
            for src, types in by_source.items():
                summary_parts.append(f"{src}:{'+'.join(sorted(types))}")

            final_result['sources'] = summary_parts
            final_result['artwork_count'] = len(downloaded)
                
            if downloaded:
                final_result['success'] = True

        except asyncio.TimeoutError:
            logger.warning(f"Artwork pipeline timed out for {title}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in artwork pipeline for {title}: {e}")

        return final_result


    async def batch_fetch_artwork(
        self,
        games: List[Dict[str, Any]]
    ) -> Dict[int, bool]:
        """Batch fetch (wrapper)"""
        results = {}
        for game in games:
            if game.get('app_id'):
                res = await self.fetch_game_art(
                    game['title'], 
                    game['app_id'], 
                    store=game.get('store'),
                    store_id=game.get('store_id')
                )
                results[game['app_id']] = res.get('success', False)
        return results
