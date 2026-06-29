"""stores/gog/galaxy_api.py — GOG Galaxy backend HTTP primitives.

Store-neutral, low-level helpers for talking to GOG's Galaxy backend
over plain ``urllib`` with the permissive SSL context the Deck's
outdated CA store needs. These originally lived in
``services/cloud_save/gog_cloud_api.py``; they live here now so the GOG
store (Layer 4) can reuse them — for cloud saves AND achievements —
without a store importing a service (Layer 5). ``gog_cloud_api`` imports
``http_json`` / ``GOG_BUILDS_URL`` back from here, and ``gog_strategy``
imports ``exchange_game_token`` / ``fetch_gog_client_creds`` from here.

Pure functions, no instance state — callers own credentials/caching.
"""
from __future__ import annotations

import gzip
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from unifideck.core.net import ssl_ctx_permissive

logger = logging.getLogger(__name__)

# Build-manifest listing → the game's Galaxy clientId/clientSecret.
GOG_BUILDS_URL = (
    "https://content-system.gog.com/products/{game_id}"
    "/os/windows/builds?generation=2"
)
# Per-user, per-game achievement list (definitions + unlock status). Same
# host Comet uploads unlocks to — so this read reflects what was earned.
_ACHIEVEMENTS_URL = (
    "https://gameplay.gog.com/clients/{client_id}/users/{user_id}/achievements"
)
# Per-user, per-game play sessions. POST adds a session (so GOG Galaxy / other
# devices reflect time played here); GET returns ``{"time_sum": <minutes>}``.
# Same host + ACCOUNT-token auth as the achievements read above. Verified live
# (June 2026): POST shape ``{"session_date": <unix_secs>, "time": <minutes>}``.
_SESSIONS_URL = (
    "https://gameplay.gog.com/games/{game_id}/users/{user_id}/sessions"
)
_GALAXY_UA = "GalaxyClient/2.0.45"


def http_json(url: str, decompress: bool = False) -> Any:
    """GET ``url`` and parse JSON, using the permissive SSL context.

    GOG's endpoints trip the Deck's outdated CA store, so we reuse the
    same permissive context the GOG store HTTP path uses. The
    content-system manifest is zlib/gzip-compressed — try the common
    decoders before parsing. Returns the decoded JSON (object or list);
    callers ``isinstance``-guard the shape they expect.
    """
    ctx = ssl_ctx_permissive("GOG Galaxy API — outdated Deck cert store")
    req = urllib.request.Request(url, headers={"User-Agent": _GALAXY_UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        raw = resp.read()
    if decompress:
        for decoder in (
            zlib.decompress,
            lambda b: zlib.decompress(b, 16 + zlib.MAX_WBITS),
            gzip.decompress,
            lambda b: b,
        ):
            try:
                raw = decoder(raw)
                break
            except Exception:  # noqa: S112 — trying each decoder; a failure just means try the next
                continue
    return json.loads(raw)


def fetch_gog_client_creds(game_id: str) -> tuple[str | None, str | None]:
    """Game's Galaxy ``(clientId, clientSecret)`` from the build manifest."""
    builds = http_json(GOG_BUILDS_URL.format(game_id=game_id))
    items = builds.get("items") if isinstance(builds, dict) else None
    if not items:
        return None, None
    link = items[0].get("link")
    if not link:
        return None, None
    manifest = http_json(link, decompress=True)
    if not isinstance(manifest, dict):
        return None, None
    cid = manifest.get("clientId")
    csec = manifest.get("clientSecret")
    return (str(cid) if cid else None, str(csec) if csec else None)


def exchange_game_token(
    client_id: str, client_secret: str, refresh_token: str,
) -> str | None:
    """Exchange the refresh token for a GAME-client-scoped access token."""
    url = "https://auth.gog.com/token?" + urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    })
    ctx = ssl_ctx_permissive("GOG token exchange — outdated Deck cert store")
    req = urllib.request.Request(url, headers={"User-Agent": _GALAXY_UA})
    with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
        data = json.loads(resp.read())
    tok = data.get("access_token") if isinstance(data, dict) else None
    return str(tok) if tok else None


def fetch_achievements_page(
    client_id: str,
    user_id: str,
    access_token: str,
    page_token: str | None = None,
) -> dict[str, Any]:
    """One page of a game's achievements for a user. 404 → empty page.

    Mirrors ``list_cloud_objects``' request shape (Bearer auth, permissive
    SSL context, GalaxyClient UA). Returns GOG's raw
    ``{total_count, limit, page_token, items:[...]}`` dict; a 404 (game with
    no achievements, or a user who never synced) yields an empty page so the
    caller can treat "no achievements" as a normal, non-error result.
    """
    url = _ACHIEVEMENTS_URL.format(client_id=client_id, user_id=user_id)
    if page_token:
        url += "?" + urllib.parse.urlencode({"page_token": page_token})
    ctx = ssl_ctx_permissive("GOG achievements — outdated Deck cert store")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": _GALAXY_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"items": [], "total_count": 0, "page_token": None}
        raise


def post_gog_session(
    user_id: str,
    game_id: str,
    access_token: str,
    session_date: int,
    minutes: int,
) -> bool:
    """Report one play session to GOG. ``True`` on a 2xx.

    POSTs ``{"session_date": <unix_secs_at_start>, "time": <minutes>}`` to the
    per-user/per-game sessions endpoint with the ACCOUNT bearer token (same auth
    as :func:`fetch_achievements_page`). Sessions are additive on GOG's side, so
    the caller must de-dupe (only push each local session once). Lets
    ``HTTPError`` propagate so the caller can refresh-and-retry on 401/403.
    """
    url = _SESSIONS_URL.format(game_id=game_id, user_id=user_id)
    body = json.dumps(
        {"session_date": session_date, "time": minutes},
    ).encode()
    ctx = ssl_ctx_permissive("GOG sessions — outdated Deck cert store")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _GALAXY_UA,
    })
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return bool(200 <= resp.status < 300)


def fetch_gog_playtime_minutes(
    user_id: str,
    game_id: str,
    access_token: str,
) -> int | None:
    """GOG's total time played for this game, in minutes (``time_sum``).

    GET the sessions endpoint → ``{"time_sum": <minutes>}``. Returns ``None`` on
    a 404 (game GOG has never seen a session for). Lets other ``HTTPError``
    propagate so the caller can refresh-and-retry on 401/403.
    """
    url = _SESSIONS_URL.format(game_id=game_id, user_id=user_id)
    ctx = ssl_ctx_permissive("GOG sessions — outdated Deck cert store")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": _GALAXY_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if isinstance(data, dict) and data.get("time_sum") is not None:
        return int(data["time_sum"])
    return None
