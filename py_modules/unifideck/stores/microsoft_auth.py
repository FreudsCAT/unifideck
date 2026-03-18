"""
Microsoft authentication helpers for Unifideck.

Provides SSL contexts, HTTP request helpers, and the Xbox Live token chain
(XBL user token → XSTS token) as pure functions with no class-level state.

These are the lowest-level building blocks — imported by both the connector
(microsoft.py).
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ssl_ctx_strict", "ssl_ctx_permissive",
    "http_post", "http_post_json", "http_get",
    "build_xbl_chain",
]

# No module-level constants — all values are passed as parameters
# from the connector, which reads them from settings.json.

# ───────────────────────── SSL contexts ──────────────────────────────────

def ssl_ctx_strict() -> ssl.SSLContext:
    """SSL context for authentication and API endpoints.

    SteamOS ships an incomplete CA bundle that cannot verify login.live.com.
    We try certifi first (bundled with many Python installs); if unavailable
    we fall back to disabling certificate verification.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    logger.warning(
        "[MS] certifi not found — disabling SSL verification for MS auth endpoints. "
        "Install certifi for proper certificate validation."
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def ssl_ctx_permissive() -> ssl.SSLContext:
    """Permissive SSL context for CDN package downloads only.

    Microsoft delivery CDN URLs sometimes present certificates that the
    Steam Deck system CA bundle cannot verify.  This context is intentionally
    restricted to unauthenticated binary downloads.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ───────────────────────── HTTP helpers ──────────────────────────────────

def http_post(url: str, data: dict, headers: dict) -> dict:
    """Synchronous HTTP POST (form-encoded) returning parsed JSON."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())


def http_post_json(url: str, payload: dict, headers: dict) -> dict:
    """Synchronous HTTP POST (JSON body) returning parsed JSON."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20, context=ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())


def http_get(url: str, headers: dict) -> dict:
    """Synchronous HTTP GET returning parsed JSON."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx_strict()) as r:
        return json.loads(r.read().decode())

# ───────────────────────── XBL / XSTS chain ─────────────────────────────

def build_xbl_chain(
    access_token: str,
    locale: str,
    xbl_auth_url: str,
    xsts_url: str,
    xbl_user_agent: str,
) -> Optional[Dict[str, str]]:
    """
    Build the XBL user token → XSTS token chain.

    Pure function — no class state.  All inputs are explicit parameters.

    Args:
        access_token: Microsoft OAuth access token.
        locale: BCP-47 locale string (e.g. 'fr-FR').
        xbl_auth_url: Xbox Live user authentication endpoint.
        xsts_url: XSTS token endpoint.
        xbl_user_agent: User-Agent header for XBL/XSTS requests.

    Returns:
        Dict with keys (xbl_token, user_hash, xsts_token, xsts_rp, xuid)
        on success, or None on failure.
    """
    logger.info("[MS] Building XBL/XSTS token chain")
    try:
        # Step A: XBL user token
        _xbl_candidates = [
            ("2", f"t={access_token}"),  # JWT / contract v2
            ("1", f"d={access_token}"),  # compact MSA / contract v1
            ("1", f"t={access_token}"),  # JWT / contract v1 fallback
        ]
        xbl_resp = None
        for _cv, _rps in _xbl_candidates:
            try:
                xbl_resp = http_post_json(
                    xbl_auth_url,
                    {
                        "Properties": {
                            "AuthMethod": "RPS",
                            "SiteName":   "user.auth.xboxlive.com",
                            "RpsTicket":  _rps,
                        },
                        "RelyingParty": "http://auth.xboxlive.com",
                        "TokenType":    "JWT",
                    },
                    {
                        "Content-Type":           "application/json",
                        "Accept":                 "application/json",
                        "x-xbl-contract-version": _cv,
                        "User-Agent":             xbl_user_agent,
                        "Accept-Language":        locale,
                    },
                )
                if xbl_resp.get("Token"):
                    logger.info(f"[MS] XBL auth OK (contract-v{_cv}, prefix={_rps[:2]!r})")
                    break
                else:
                    logger.debug(f"[MS] XBL no token (v{_cv}, {_rps[:2]!r}): {xbl_resp}")
                    xbl_resp = None
            except urllib.error.HTTPError as e:
                try:
                    _body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    _body = ""
                logger.debug(f"[MS] XBL failed (v{_cv}, {_rps[:2]!r}): HTTP {e.code} {_body[:500]}")
                xbl_resp = None
            except Exception as e:
                logger.debug(f"[MS] XBL failed (v{_cv}, {_rps[:2]!r}): {e}")
                xbl_resp = None

        if xbl_resp is None or not xbl_resp.get("Token"):
            logger.error("[MS] XBL user token failed with all contract/prefix combos")
            return None

        xbl_token = xbl_resp["Token"]
        display_claims = xbl_resp.get("DisplayClaims", {})
        xui = display_claims.get("xui", [{}])
        user_hash = xui[0].get("uhs") if xui else None

        logger.info(f"[MS] ✓ XBL user token obtained (uhs={user_hash})")

        # Step B: XSTS token with generic RP (Title Hub + FE3).
        _xsts_headers = {
            "Content-Type":           "application/json",
            "Accept":                 "application/json",
            "x-xbl-contract-version": "1",
            "User-Agent":             xbl_user_agent,
            "Accept-Language":        locale,
        }
        xsts_resp = None
        xsts_rp = "http://xboxlive.com"
        try:
            xsts_resp = http_post_json(
                xsts_url,
                {
                    "Properties": {
                        "SandboxId":  "RETAIL",
                        "UserTokens": [xbl_token],
                    },
                    "RelyingParty": xsts_rp,
                    "TokenType":    "JWT",
                },
                _xsts_headers,
            )
            logger.info(f"[MS] ✓ XSTS obtained with RP={xsts_rp!r} sandbox='RETAIL'")
        except urllib.error.HTTPError as e:
            try:
                _body = e.read().decode("utf-8", errors="replace")
            except Exception:
                _body = ""
            logger.warning(f"[MS] XSTS failed (RP={xsts_rp!r}): HTTP {e.code} {_body[:500]}")
            return None
        except Exception as e:
            logger.warning(f"[MS] XSTS failed (RP={xsts_rp!r}): {e}")
            return None

        # XSTS error codes
        if "XErr" in xsts_resp:
            xerr = xsts_resp["XErr"]
            logger.error(f"[MS] XSTS error code: {xerr}")
            if xerr == 2148916238:
                logger.error("[MS] Account has no Xbox profile — create one at xbox.com")
            elif xerr == 2148916233:
                logger.error("[MS] Account is from a country where Xbox is not available")
            return None

        xsts_token = xsts_resp.get("Token")
        if not xsts_token:
            logger.error(f"[MS] XSTS token missing: {xsts_resp}")
            return None

        xsts_claims = xsts_resp.get("DisplayClaims", {}).get("xui", [{}])
        xuid = xsts_claims[0].get("xid") if xsts_claims else None

        logger.info(f"[MS] ✓ XSTS token obtained (xuid={xuid})")
        return {
            "xbl_token":  xbl_token,
            "user_hash":  user_hash,
            "xsts_token": xsts_token,
            "xsts_rp":    xsts_rp,
            "xuid":       xuid,
        }

    except Exception as e:
        logger.error(f"[MS] XBL chain error: {e}", exc_info=True)
        return None
