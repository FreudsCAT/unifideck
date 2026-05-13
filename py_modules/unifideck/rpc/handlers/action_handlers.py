"""ActionHandlers — dispatch ``unifideck://`` deep-link verbs.

OP-25b | py_modules/unifideck/rpc/handlers/action_handlers.py

When the frontend (or an external app) invokes a
``unifideck://verb/arg1/arg2`` URI, the call lands here. The
class parses the URI, validates the scope (frontend-scope
verbs are refused — the frontend handles those locally), and
routes to a per-verb private handler.

Supported verbs:

* ``auth`` — trigger the auth-start flow for a given store;
* ``retry-sync`` — retry a failed cloud-save sync for one
  game (``sync_down`` or ``sync_up``);
* ``refresh-library`` — schedule a single-store sync in the
  background;
* ``refresh-all-libraries`` — schedule a full library sync.

Unknown verbs raise ``unhandled_backend_verb`` so the
frontend can surface a typed error rather than silently
ignoring.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from unifideck.actions.unifideck_uri import (
    SCOPE_FRONTEND,
    ParsedAction,
    parse_unifideck_uri,
)
from unifideck.rpc.handlers.base import RpcHandlerBase
from unifideck.rpc.wrapper import RpcError


class ActionHandlers(RpcHandlerBase):
    """Route ``unifideck://`` URIs to per-verb backend handlers."""

    async def dispatch_unifideck_action(self, uri: str) -> Any:
        """Parse a ``unifideck://`` URI and dispatch to the verb handler.

        Three-step pipeline:

        1. **Parse** — ``parse_unifideck_uri`` returns a
           typed ``ParsedAction`` with ``valid``, ``scope``,
           ``verb``, ``args``, and ``error`` fields.
        2. **Validate** — refuse invalid URIs
           (``invalid_uri``) and frontend-scope verbs
           (``frontend_scope_verb``) which the frontend
           should have handled itself.
        3. **Dispatch** — resolve the per-verb handler and
           await it. The result is cast to ``dict`` because
           the RPC wrapper expects JSON-friendly output.

        Args:
            uri: the full ``unifideck://...`` URI string.

        Returns:
            Per-verb result dict.

        Raises:
            RpcError: on malformed URIs, frontend-scope verbs,
                or unknown backend verbs (raised inside
                ``_resolve_verb_handler``).
        """
        action = parse_unifideck_uri(uri)
        if not action.valid:
            raise RpcError("invalid_uri", reason=action.error, uri=uri)
        if action.scope == SCOPE_FRONTEND:
            raise RpcError(
                "frontend_scope_verb",
                verb=action.verb,
                hint="frontend should handle settings/* locally",
            )
        handler = self._resolve_verb_handler(action.verb)
        return cast(dict, await handler(action))

    def _resolve_verb_handler(self, verb: str):
        """Look up the bound-method handler for a verb name.

        The verb→method table is built inline rather than as
        a class attribute so the bound methods reference
        ``self``; this is also why the method isn't a
        ``@staticmethod``.

        Args:
            verb: verb extracted from the parsed URI.

        Returns:
            The bound async method for that verb.

        Raises:
            RpcError: ``code="unhandled_backend_verb"`` with
                hint pointing at this file when the verb is
                unknown — a strong signal that adding a verb
                also requires adding a handler here.
        """
        handlers = {
            "auth": self._handle_auth,
            "retry-sync": self._handle_retry_sync,
            "refresh-library": self._handle_refresh_library,
            "refresh-all-libraries": self._handle_refresh_all_libraries,
        }
        if verb not in handlers:
            raise RpcError(
                "unhandled_backend_verb",
                verb=verb,
                hint="add a handler in ActionHandlers",
            )
        return handlers[verb]

    async def _handle_auth(self, action: ParsedAction) -> Any:
        """Start the auth flow for the store named in ``action.args[0]``.

        Delegates to ``registry.auth_action`` which knows
        per-store auth wiring (OAuth, CDP-driven, etc.).

        Args:
            action: parsed action; ``args[0]`` is the store id.

        Returns:
            The auth-action result dict from the registry.
        """
        store = action.args[0]
        return cast(dict, await self._registry.auth_action(store, "start"))

    async def _handle_retry_sync(self, action: ParsedAction) -> Any:
        """Retry a failed cloud-save sync for one game.

        Expects ``(store, game_id, phase)`` in
        ``action.args``. The ``phase`` must be either
        ``"sync_down"`` or ``"sync_up"``; anything else
        raises ``invalid_phase``. Requires the cloud-save
        service to be available — raises
        ``service_unavailable`` otherwise.

        Args:
            action: parsed action with 3 args.

        Returns:
            ``{success, error, store, game_id, phase}`` dict
            (the cloud-save service's ``Result`` flattened
            with the request context for the frontend).
        """
        store, game_id, phase = action.args
        svc = self._services.cloudsave
        if svc is None:
            raise RpcError("service_unavailable", service="cloudsave")
        if phase == "sync_down":
            result = await svc.sync_down(store, game_id)
        elif phase == "sync_up":
            result = await svc.sync_up(store, game_id)
        else:
            raise RpcError(
                "invalid_phase",
                phase=phase,
                supported=["sync_down", "sync_up"],
            )
        return {
            "success": result.success,
            "error": result.error,
            "store": store,
            "game_id": game_id,
            "phase": phase,
        }

    async def _handle_refresh_library(self, action: ParsedAction) -> Any:
        """Schedule a single-store library sync in the background.

        Fire-and-forget: spawns the sync task and returns
        immediately with ``status="scheduled"``. The actual
        sync runs in its own task with a named identifier
        for diagnostics (``refresh-library-<store>``).

        Args:
            action: parsed action; ``args[0]`` is the store id.

        Returns:
            ``{success: True, store, status: "scheduled"}``.
        """
        store = action.args[0]
        asyncio.create_task(
            self._sync.sync_single_store(store),
            name=f"refresh-library-{store}",
        )
        return {"success": True, "store": store, "status": "scheduled"}

    async def _handle_refresh_all_libraries(self, _action: ParsedAction) -> Any:
        """Schedule a full multi-store library sync in the background.

        Same fire-and-forget pattern as
        ``_handle_refresh_library`` but uses
        ``self._sync.sync_all()``. The ``action`` argument
        is unused (no per-store filter) — named ``_action``
        to signal that.

        Args:
            _action: parsed action (unused).

        Returns:
            ``{success: True, status: "scheduled"}``.
        """
        asyncio.create_task(
            self._sync.sync_all(),
            name="refresh-all-libraries",
        )
        return {"success": True, "status": "scheduled"}
