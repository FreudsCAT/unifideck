from __future__ import annotations
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)
SCOPE_BACKEND = "backend"
SCOPE_FRONTEND = "frontend"

@dataclass(frozen=True)
class ParsedAction:
    """Parsed action."""
    valid: bool
    verb: str = ""
    scope: str = ""
    args: tuple[str, ...] = ()
    query: dict[str, str] = field(default_factory=dict)
    error: str = ""

_VERB_REGISTRY: dict[str, tuple[str, int, int, str]] = {
    "auth": (
        SCOPE_BACKEND, 1, 1,
        "Start OAuth flow for a store. Args: <store>.",
    ),
    "retry-sync": (
        SCOPE_BACKEND, 3, 3,
        "Retry a cloud save sync. Args: <store> <game_id> <phase>. "
        "Phase is 'sync_down' or 'sync_up'.",
    ),
    "refresh-library": (
        SCOPE_BACKEND, 1, 1,
        "Refresh the game library for a single store. Args: "
        "<store>. Fire-and-forget: the RPC returns immediately "
        "and the sync runs in the background; the UI Library "
        "view picks up the result through its own SYNC_PROGRESS "
        "event subscription.",
    ),
    "refresh-all-libraries": (
        SCOPE_BACKEND, 0, 0,
        "Refresh every registered store's library. Args: none. "
        "Fire-and-forget same as refresh-library, but drives "
        "SyncService.sync_all() across all stores in parallel. "
        "Wired to the 'Refresh all libraries' button in the "
        "UnifideckSettingsPanel.",
    ),
    "open-save-folder": (
        SCOPE_FRONTEND, 2, 2,
        "Open the SaveFolderModal for a game. Args: <store> "
        "<game_id>. Frontend-only: the listener component "
        "opens the modal via Decky's showModal helper; the "
        "modal itself calls the backend list_save_folder RPC "
        "to populate its data.",
    ),
    "show-logs": (
        SCOPE_FRONTEND, 1, 1,
        "Open the LaunchLogsModal for a past launch. Args: "
        "<launch_id>. Frontend-only: the listener component "
        "opens the modal, which fetches logs via the backend "
        "get_launch_logs RPC. Used by launcher-failure toast "
        "actions on errorCircuitBreakerOpen and generic "
        "LAUNCHER_ERROR codes.",
    ),
    "settings": (
        SCOPE_FRONTEND, 1, 2,
        "Navigate to a settings section. Args: <section> "
        "[<focus_target>]. Frontend-only.",
    ),
}

def list_supported_verbs() -> list[str]:
    """List supported verbs."""
    return sorted(_VERB_REGISTRY.keys())

def parse_unifideck_uri(uri: str) -> ParsedAction:
    """Parse unifideck URI."""
    if not uri:
        return ParsedAction(valid=False, error="empty_uri")
    try:
        parsed = urlparse(uri)
    except Exception as err:
        return ParsedAction(valid=False, error=f"parse_error:{err}")
    if parsed.scheme != "unifideck":
        return ParsedAction(
            valid=False,
            error=f"wrong_scheme:{parsed.scheme}"
        )
    verb = parsed.netloc
    if not verb:
        return ParsedAction(valid=False, error="missing_verb")
    if verb not in _VERB_REGISTRY:
        return ParsedAction(
            valid=False, verb=verb,
            error=f"unknown_verb:{verb}",
        )
    scope, min_args, max_args, _doc = _VERB_REGISTRY[verb]
    raw_path = parsed.path.lstrip("/")
    args = tuple(p for p in raw_path.split("/") if p) if raw_path else ()
    if not (min_args <= len(args) <= max_args):
        return ParsedAction(
            valid=False, verb=verb, scope=scope,
            error=(
                f"wrong_arg_count:got_{len(args)}_"
                f"expected_{min_args}_to_{max_args}"
            ),
        )
    raw_query = parse_qs(parsed.query) if parsed.query else {}
    query = {k: v[0] for k, v in raw_query.items() if v}
    return ParsedAction(
        valid=True, verb=verb, scope=scope, args=args, query=query,
    )
