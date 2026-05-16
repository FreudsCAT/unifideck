import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

from unifideck.core.net import ssl_ctx_permissive as _ssl

logger = logging.getLogger(__name__)
__all__ = [
    "build_xbl_chain",
    "http_get",
    "http_post",
    "request_xsts_token",
]
def http_post(url: str, data: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
    """Http post."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=_ssl("Microsoft OAuth — outdated Deck cert store")) as r:
        return cast(dict[str, Any], json.loads(r.read().decode()))
def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, Any]) -> dict[str, Any]:
    """Http post JSON."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20, context=_ssl("Microsoft OAuth — outdated Deck cert store")) as r:
        return cast(dict[str, Any], json.loads(r.read().decode()))
def http_get(url: str, headers: dict[str, Any]) -> dict[str, Any]:
    """Http get."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=_ssl("Microsoft OAuth — outdated Deck cert store")) as r:
        return cast(dict[str, Any], json.loads(r.read().decode()))

def build_xbl_chain(
    access_token: str,
    locale: str,
    xbl_auth_url: str,
    xsts_url: str,
    xbl_user_agent: str,
    xsts_relying_party: str = "http://xboxlive.com",
) -> dict[str, str] | None:

    """Build XBL chain."""
    logger.info("[MS] Building XBL/XSTS token chain")
    try:
        xbl_resp = _obtain_xbl_user_token(
            access_token, locale, xbl_auth_url, xbl_user_agent,
        )
        if xbl_resp is None:
            return None
        xbl_token = xbl_resp["Token"]
        user_hash = _extract_user_hash(xbl_resp)
        logger.info(
            "[MS] ✓ XBL user token obtained (uhs=%s)", user_hash,
        )
        xsts_rp = xsts_relying_party
        xsts_resp = _request_xsts_token(
            xbl_token, xsts_rp, locale, xsts_url, xbl_user_agent,
        )
        if xsts_resp is None:
            return None
        if "XErr" in xsts_resp:
            _log_xsts_xerr(xsts_resp["XErr"])
            return None
        xsts_token = xsts_resp.get("Token")
        if not xsts_token:
            logger.error(
                "[MS] XSTS token missing: %s", xsts_resp,
            )
            return None
        xsts_claims = xsts_resp.get(
            "DisplayClaims", {},
        ).get("xui", [{}])
        xuid = (
            xsts_claims[0].get("xid") if xsts_claims else None
        )
        logger.info(
            "[MS] ✓ XSTS token obtained (xuid=%s)", xuid,
        )
        return {
            "xbl_token": xbl_token,
            "user_hash": user_hash,
            "xsts_token": xsts_token,
            "xsts_rp": xsts_rp,
            "xuid": xuid,
        }
    except Exception:
        logger.exception("[MS] XBL chain error")
        return None
def request_xsts_token(
    xbl_token: str,
    xsts_rp: str,
    locale: str,
    xsts_url: str,
    xbl_user_agent: str,
) -> dict[str, Any] | None:
    """Request XSTS token."""
    return _request_xsts_token(
        xbl_token, xsts_rp, locale, xsts_url, xbl_user_agent,
    )

def _obtain_xbl_user_token(
    access_token: str,
    locale: str,
    xbl_auth_url: str,
    xbl_user_agent: str,
) -> dict[str, Any] | None:

    """Obtain XBL user token."""
    candidates = [
        ("2", f"t={access_token}"),
        ("1", f"d={access_token}"),
        ("1", f"t={access_token}"),
    ]
    for contract_v, rps in candidates:
        resp = _try_xbl_request(
            contract_v, rps, locale, xbl_auth_url, xbl_user_agent,
        )
        if resp is not None and resp.get("Token"):
            logger.info(
                "[MS] XBL auth OK (contract-v%s, prefix=%r)",
                contract_v, rps[:2],
            )
            return resp
    logger.error(
        "[MS] XBL user token failed with all contract/prefix combos",
    )
    return None
def _try_xbl_request(
    contract_v: str,
    rps: str,
    locale: str,
    xbl_auth_url: str,
    xbl_user_agent: str,
) -> dict[str, Any] | None:
    """Try XBL request."""
    body = {
        "Properties": {
            "AuthMethod": "RPS",
            "SiteName": "user.auth.xboxlive.com",
            "RpsTicket": rps,
        },
        "RelyingParty": "http://auth.xboxlive.com",
        "TokenType": "JWT",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-xbl-contract-version": contract_v,
        "User-Agent": xbl_user_agent,
        "Accept-Language": locale,
    }
    try:
        return http_post_json(xbl_auth_url, body, headers)
    except urllib.error.HTTPError as e:
        body_text = _read_http_error_body(e)
        logger.debug(
            "[MS] XBL failed (v%s, %r): HTTP %d %s",
            contract_v, rps[:2], e.code, body_text[:500],
        )
        return None
    except Exception as e:
        logger.debug(
            "[MS] XBL failed (v%s, %r): %s",
            contract_v, rps[:2], e,
        )
        return None
def _extract_user_hash(xbl_resp: dict[str, Any]) -> str | None:
    """Extract user hash."""
    display_claims = xbl_resp.get("DisplayClaims", {})
    xui = display_claims.get("xui", [{}])
    return xui[0].get("uhs") if xui else None

def _request_xsts_token(
    xbl_token: str,
    xsts_rp: str,
    locale: str,
    xsts_url: str,
    xbl_user_agent: str,
) -> dict[str, Any] | None:

    """Request XSTS token."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-xbl-contract-version": "1",
        "User-Agent": xbl_user_agent,
        "Accept-Language": locale,
    }
    body = {
        "Properties": {
            "SandboxId": "RETAIL",
            "UserTokens": [xbl_token],
        },
        "RelyingParty": xsts_rp,
        "TokenType": "JWT",
    }
    try:
        resp = http_post_json(xsts_url, body, headers)
        logger.info(
            "[MS] ✓ XSTS obtained with RP=%r sandbox='RETAIL'",
            xsts_rp,
        )
        return resp
    except urllib.error.HTTPError as e:
        body_text = _read_http_error_body(e)
        logger.warning(
            "[MS] XSTS failed (RP=%r): HTTP %d %s",
            xsts_rp, e.code, body_text[:500],
        )
        return None
    except Exception as e:
        logger.warning(
            "[MS] XSTS failed (RP=%r): %s", xsts_rp, e,
        )
        return None
def _log_xsts_xerr(xerr: int) -> None:
    """Log XSTS xerr."""
    logger.error("[MS] XSTS error code: %d", xerr)
    if xerr == 2148916238:
        logger.error(
            "[MS] Account has no Xbox profile — create one at xbox.com",
        )
    elif xerr == 2148916233:
        logger.error(
            "[MS] Account is from a country where Xbox is not available",
        )
def _read_http_error_body(err: urllib.error.HTTPError) -> str:
    """Read http error body."""
    try:
        return err.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
