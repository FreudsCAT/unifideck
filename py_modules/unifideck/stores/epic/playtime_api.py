"""stores/epic/playtime_api.py — report play sessions to Epic (+ read totals).

Epic's ``library-service`` accepts playtime over the same session-delta model as
GOG — confirmed live (June 2026): both routes answer 401 (not 404) without auth.
Source for the writable ``PUT`` (the Epic launcher's own playtime ingest):
``hawkeye116477/playnite-legendary-plugin``. Auth is the legendary launcher OAuth
token (``user.json``), resolved/refreshed by ``EpicSessions``.

Like ``epic/achievements.py``, requests go out via a ``curl`` subprocess with a
scrubbed env — the Decky runtime's ``LD_LIBRARY_PATH`` pulls the Steam Runtime's
old libssl, which Epic edges can reject — with plain ``urllib`` as the fallback.

Pure HTTP, no creds resolution (that's ``EpicSessions``' job).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from unifideck.core.net import ssl_ctx_permissive

logger = logging.getLogger(__name__)

_PLAYTIME_BASE = (
    "https://library-service.live.use1a.on.epicgames.com"
    "/library/api/public/playtime/account"
)
# A launcher UA — this is the launcher's own backend.
_UA = "EpicGamesLauncher/15.17.1-35104619+++Portal+Release-Live"
_TIMEOUT = 15


def put_epic_session(
    account_id: str,
    artifact_id: str,
    token_type: str,
    access_token: str,
    start_iso: str,
    end_iso: str,
    machine_id: str,
) -> int | None:
    """Report one play session to Epic. Returns the HTTP status (``None`` on a
    transport failure) so the caller can distinguish 401 (refresh) from success.

    PUTs ``{machineId, artifactId, startTime, endTime}`` (ISO-8601 UTC) to
    ``…/playtime/account/{account_id}``.
    """
    url = f"{_PLAYTIME_BASE}/{account_id}"
    body = json.dumps({
        "machineId": machine_id,
        "artifactId": artifact_id,
        "startTime": start_iso,
        "endTime": end_iso,
    }).encode()
    code, _ = _epic_request("PUT", url, token_type, access_token, body)
    return code


def fetch_epic_playtime_all(
    account_id: str,
    token_type: str,
    access_token: str,
) -> tuple[int | None, dict[str, int]]:
    """All of this account's per-game totals. ``(http_status, {artifactId: secs})``.

    ``totalTime`` from Epic is seconds. Status is returned so the caller can
    refresh-and-retry on 401; the map is empty on any non-200.
    """
    url = f"{_PLAYTIME_BASE}/{account_id}/all"
    code, raw = _epic_request("GET", url, token_type, access_token, None)
    if code != 200 or not raw:
        return code, {}
    try:
        data = json.loads(raw)
    except ValueError:
        return code, {}
    out: dict[str, int] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("artifactId") is not None:
                out[str(item["artifactId"])] = int(item.get("totalTime") or 0)
    return code, out


def _epic_request(
    method: str,
    url: str,
    token_type: str,
    access_token: str,
    body: bytes | None,
) -> tuple[int | None, str | None]:
    """Issue an authed request via curl (scrubbed env) → urllib fallback.

    Returns ``(http_status, response_body)``; ``(None, None)`` on a transport
    failure. ``LD_LIBRARY_PATH`` / ``LD_PRELOAD`` are stripped for curl so it
    links the system libssl rather than the Steam Runtime's (see module docs).
    """
    auth = f"{token_type} {access_token}"
    curl = shutil.which("curl")
    if curl:
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")
        }
        args = [
            curl, "-sS", "--max-time", str(_TIMEOUT), "-X", method, url,
            "-H", f"Authorization: {auth}",
            "-H", "Content-Type: application/json",
            "-H", f"User-Agent: {_UA}",
            "-w", "\n%{http_code}",
        ]
        if body is not None:
            args += ["--data-binary", "@-"]
        try:
            proc = subprocess.run(
                args, input=body, capture_output=True, env=env,
                timeout=_TIMEOUT + 5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.info("[epic.playtime] curl %s %s failed: %s", method, url, e)
            return None, None
        out = proc.stdout.decode(errors="ignore")
        nl = out.rfind("\n")
        if nl < 0:
            return None, None
        try:
            code = int(out[nl + 1:].strip())
        except ValueError:
            code = 0
        return code, out[:nl]
    return _request_via_urllib(method, url, auth, body)


def _request_via_urllib(
    method: str, url: str, auth: str, body: bytes | None,
) -> tuple[int | None, str | None]:
    """Fallback for environments without curl (dev shells)."""
    ctx = ssl_ctx_permissive("Epic playtime — outdated Deck cert store")
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
            return resp.status, resp.read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.info("[epic.playtime] urllib %s %s failed: %s", method, url, e)
        return None, None
