"""core/types/events.py — Event names and status enums.

The enums are intentionally kept together in one module because
they're conceptually related (string-typed taxonomies) and none of
them pulls in any runtime dependency — pure value types. A future
split into one-enum-per-file would add import noise without
improving cohesion.

Every enum inherits from `str` so members serialize directly to
JSON without a custom encoder: `json.dumps(Events.SYNC_COMPLETE)`
produces `"sync_complete"`, exactly what the frontend expects.

Reference: Technical Document v1.0 — Section 3.3 (EventBus topology).
"""

from __future__ import annotations

from enum import StrEnum


class Events(StrEnum):
    """All event names emitted on the EventBus.

    ORG: grouped by concern. Adding a new event = one line here +
    a handler subscription somewhere. The `str` base makes the
    name equal to the enum value, which is what subscribers match
    against.

    The frontend mirrors these exact string values in
    `src/SteamBridge.ts` — changing a value here is a breaking
    change for any unreleased frontend build.
    """

    # Plugin lifecycle
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADING = "plugin_unloading"

    # Sync lifecycle
    SYNC_STARTED = "sync_started"
    SYNC_PROGRESS = "sync_progress"
    SYNC_COMPLETE = "sync_complete"
    SYNC_FAILED = "sync_failed"
    SYNC_CANCELLED = "sync_cancelled"
    # Post-sync enrichment phases — emitted by ArtworkService and
    # MetadataService so the frontend progress bar stays alive
    # through artwork downloads + metadata extraction. Payload:
    #  { phase: "artwork"|"metadata", active: bool, total: int|None, done: int|None }
    POST_SYNC_PHASE_CHANGED = "post_sync_phase_changed"

    # Fired by the fire-and-forget Metacritic backfill
    # (``metadata_backfill``) once its long-tail metacritic.com lookups
    # have all landed in the ``metadata`` cache — AFTER the sync's
    # progress bar already hit 100%. The frontend re-reads library
    # facets on this so newly-backfilled scores surface in Steam's
    # native Sort-by-Metacritic without a manual resync/restart.
    METADATA_BACKFILL_COMPLETE = "metadata_backfill_complete"

    # Durable activity-log events — captured by ActivityLogService
    # into a JSONL file (``runtime_dir/sync_activity.log``) so the
    # frontend can show "last 10 syncs" with timestamps, durations,
    # and per-store counts. Distinct from SYNC_STARTED /
    # SYNC_COMPLETE which are ephemeral UI signals; these carry the
    # data worth persisting.
    #   started   payload: { source, stores, started_at_ms }
    #   completed payload: { source, duration_ms, game_count, errors }
    #   cancelled payload: { source, duration_ms }
    LIBRARY_SYNC_STARTED = "library_sync_started"
    LIBRARY_SYNC_COMPLETED = "library_sync_completed"
    LIBRARY_SYNC_CANCELLED = "library_sync_cancelled"

    # Store auth lifecycle
    STORE_AUTH_STARTED = "store_auth_started"
    STORE_AUTH_COMPLETE = "store_auth_complete"
    STORE_AUTH_FAILED = "store_auth_failed"
    STORE_LOGOUT = "store_logout"

    # Store registration lifecycle — emitted by StoreRegistry
    # when a store plugin is registered at bootstrap. Consumed
    # by metrics_collector.py and any future store-aware
    # dashboards.
    STORE_REGISTERED = "store_registered"

    # Game lifecycle
    GAME_INSTALLED = "game_installed"
    GAME_UNINSTALLED = "game_uninstalled"
    GAME_UPDATE_AVAILABLE = "game_update_available"
    GAME_LAUNCHED = "game_launched"
    GAME_STOPPED = "game_stopped"
    PLAYTIME_UPDATED = "playtime_updated"
    # Playtime → store sync (GOG/Epic) outcome, per drain.
    PLAYTIME_SYNC_COMPLETE = "playtime_sync_complete"
    PLAYTIME_SYNC_FAILED = "playtime_sync_failed"

    # Power/Sleep lifecycle
    SUSPEND = "suspend"
    RESUME = "resume"

    # Launcher progress stages + toast bridge.
    # Emitted by LauncherService and cloud_failure.py as a
    # game moves through the launch pipeline (prefix setup,
    # cloud sync, proton selection, umu-run start, ...).
    # Also emitted on cloud sync failures, disk space checks,
    # and circuit breaker events. The frontend's
    # LauncherToastListener subscribes to this channel to
    # render toast notifications with optional action buttons
    # (see actions/unifideck_uri.py for the URI scheme).
    # Payload fields: i18n_key (str), i18n_title_key? (str — bold
    # toast title rendered above i18n_key's message), severity
    # ("info"|"warning"|"error"), i18n_params (dict),
    # duration_ms (int), action? ({i18n_label_key, target_url,
    # fallback_url?}), store?, game_id?, phase?.
    LAUNCHER_STAGE = "launcher_stage"

    # Per-game circuit breaker state transitions. Emitted by
    # LaunchHistoryService whenever the breaker opens, closes,
    # is bypassed, or is manually reset. The frontend's
    # useCircuitState hook subscribes to this channel filtered
    # by game_key to drive the PlayButtonOverride badge +
    # buttons in real-time (no polling). Replacing the 30s
    # poll with push means the badge appears/disappears
    # instantly on user actions (Reset, Force launch) and on
    # launch-level state changes (crash → open, success → close).
    #
    # Payload fields:
    #   game_key (str)  — "<store>:<game_id>"
    #   state (str)     — "open" | "closed" | "bypassed"
    #   recent_count (int) — failures in window
    #   failure_kinds (list[str]) — e.g. ["fast_boot", "fast_boot"]
    #   trigger (str)   — what caused the transition:
    #     "record_failure", "record_success", "clear_failures",
    #     "arm_bypass", "consume_bypass", "window_expired"
    CIRCUIT_STATE_CHANGED = "circuit_state_changed"

    # Download lifecycle
    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_PROGRESS = "download_progress"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_CANCELLED = "download_cancelled"

    # Ubisoft install — frontend RunGame trigger. UPC must be opened
    # via Steam's RunGame so it gets its own gamescope/XWayland session
    # in Gaming Mode (a bare backend subprocess has no session to render
    # into → invisible window — the install-never-appears bug). RunGame
    # is a frontend SteamClient API, so the download worker emits this
    # once it has bootstrapped the per-game prefix; the frontend reacts
    # by calling ``launchUbisoftInstallViaShortcut`` with an ``install``
    # action and the worker then monitors the filesystem for the install.
    # Payload fields: store_game_id (str — "ubisoft:<game_id>").
    BATTLENET_INSTALL_LAUNCH_REQUESTED = "battlenet_install_launch_requested"
    UBISOFT_INSTALL_LAUNCH_REQUESTED = "ubisoft_install_launch_requested"

    # Generic store error
    STORE_ERROR = "store_error"

    RUNTIME_PROBES_REPORTED = "runtime_probes_reported"

    # security audit events. Emitted by the
    # security package + token managers + auth flows. Consumed
    # by SecurityService for audit logging, counters, and
    # centralised policy enforcement.
    SECURITY_TOKEN_ENCRYPTED = "security_token_encrypted"  # noqa: S105 — event name constant, not a credential
    SECURITY_TOKEN_DECRYPTED = "security_token_decrypted"  # noqa: S105 — event name constant, not a credential
    SECURITY_DECRYPT_FAILED = "security_decrypt_failed"
    SECURITY_TOKEN_FILE_MIGRATED = "security_token_file_migrated"  # noqa: S105 — event name constant, not a credential
    SECURITY_LEGACY_PLAINTEXT_DETECTED = "security_legacy_plaintext_detected"
    SECURITY_AUTH_FLOW_STARTED = "security_auth_flow_started"
    SECURITY_AUTH_FLOW_COMPLETED = "security_auth_flow_completed"
    SECURITY_AUTH_FLOW_FAILED = "security_auth_flow_failed"
    # token age policy. Emitted by token managers when a load
    # finds a payload whose `_unifideck_encrypted_at` metadata is
    # older than the manager's configured ``max_token_age``. The
    # token file is treated as unusable (forced re-auth) and the
    # event is surfaced to the audit log + counters so operators
    # can correlate "user kicked out" with the policy decision
    # rather than guessing it was a server-side revocation.
    SECURITY_TOKEN_AGE_EXCEEDED = "security_token_age_exceeded"  # noqa: S105 — event name constant, not a credential

    # active policy events. Emitted either by
    # token managers (permissions check at each save) or by
    # SecurityService itself when a policy triggers an action.
    SECURITY_PERMISSIONS_CHECK = "security_permissions_check"
    SECURITY_PERMISSIONS_REPAIRED = "security_permissions_repaired"
    SECURITY_BRUTEFORCE_SUSPECTED = "security_bruteforce_suspected"
    SECURITY_DEVICE_RESET_DETECTED = "security_device_reset_detected"
    SECURITY_FINGERPRINT_INITIALIZED = "security_fingerprint_initialized"

    # observability for stores whose credentials are
    # managed by external CLIs (legendary/nile) or Wine prefixes
    # (Ubisoft Connect). Unifideck does not own these tokens but
    # it does read their status at every sync, and anomalies in
    # those reads are worth tracking for diagnostics. Emitted
    # only on REAL anomalies (missing CLI binary, corrupt file,
    # missing prefix assets) — NOT on the routine "user isn't
    # logged in yet" case, which would pollute the audit log.
    SECURITY_EXTERNAL_AUTH_CHECK_FAILED = "security_external_auth_check_failed"

    # config validation at boot. Emitted by
    # ConfigValidator.validate_config after schema validation
    # completes, regardless of outcome. Handlers live in
    # SecurityService (or future ConfigService) and record the
    # result in the audit log for operator diagnostics. The
    # _COMPLETED variant carries defaults_validated + user_overrides_present
    # flags; _FAILED additionally carries error_count + first_error_source
    # + first_error_path so operators can jump to the broken section
    # without parsing the full errors list.
    CONFIG_VALIDATION_COMPLETED = "config_validation_completed"
    CONFIG_VALIDATION_FAILED = "config_validation_failed"

    # Sprint 18e — subscription lifecycle.
    # Emitted by MicrosoftSubscriptionService whenever the detected
    # tier changes for the active Microsoft account. Subscribers are
    # the frontend toast listener (informational notifications) and
    # MetricsCollector (counter of state transitions per tier).
    # Payload fields:
    #   SUBSCRIPTION_DETECTED: store (str), tier (str: "ultimate",
    #     "premium", "essential", "active_unknown")
    #   SUBSCRIPTION_EXPIRED:  store (str)
    #   SUBSCRIPTION_CHECK_FAILED: store (str), reason (str:
    #     "network", "timeout", "http_error", "bad_response",
    #     "gssv_chain_failed", "unknown")
    SUBSCRIPTION_DETECTED = "subscription_detected"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_CHECK_FAILED = "subscription_check_failed"

    # Sprint 18e — generic "store chose not to sync" event.
    # Distinct from SYNC_FAILED (which implies an error): SYNC_SKIPPED
    # is an intentional no-op with a user-facing explanation. Today
    # emitted only by MicrosoftStore when the Game Pass subscription
    # check returns NONE, ACTIVE_UNKNOWN, or an error. Future
    # subscription-based stores (EA Play, Ubisoft+) would emit the
    # same event with their own reason string.
    # Payload fields: store (str), reason (str)
    SYNC_SKIPPED = "sync_skipped"

    # Steam account switch detection. Emitted by AccountService when
    # the user signs into a different Steam account (detected by
    # polling loginusers.vdf for a MostRecent user id change).
    # Every store-scoped cache subscribes and invalidates entries for
    # the previous account so library/subscription/token state does
    # not leak across Steam profiles.
    # Payload fields:
    #   previous_user_id (str | None)  — the id that was active
    #   active_user_id (str)           — the new MostRecent id
    ACCOUNT_SWITCHED = "account_switched"

    # ShortcutService lifecycle. Emitted whenever a shortcut is added
    # or removed from shortcuts.vdf so interested services
    # (ArtworkService, MetricsCollector) can react without polling.
    # Payload fields for SHORTCUT_CREATED:
    #   store (str), app_id (int, signed), unsigned_id (int, u32),
    #   title (str), is_auth (bool)
    SHORTCUT_CREATED = "shortcut_created"

    # Emitted by ShortcutService when an entry is removed from
    # games.map (and consequently from shortcuts.vdf on the next
    # save). Mirrors SHORTCUT_CREATED so interested services
    # (ArtworkService, MetricsCollector) can react without polling.
    # Added 2026-05-15 (lot 12c): the emit site in
    # services/shortcut/games_map_mixin.py:233 has always referenced
    # ``Events.SHORTCUT_REMOVED`` but the enum member was never
    # declared — the call was a silent no-op (mypy attr-defined).
    # Payload fields: app_id (int, signed).
    SHORTCUT_REMOVED = "shortcut_removed"

    # Emitted by ShortcutService when an existing shortcut's
    # install state flips (post-install or post-uninstall) without
    # the shortcut itself being created or removed. The shortcut
    # appid stays anchored on (launcher_path, "<store>:<store_game_id>")
    # across the transition — see SyncService._backfill_app_ids —
    # so this event is the canonical channel for "the game at app_id
    # N just became (un)installed". SyncService updates _all_games
    # and the frontend refreshes its unifideckGameCache entry.
    # Payload fields:
    #   store (str), store_game_id (str), app_id (int, signed),
    #   installed (bool), exe_path (str, "" on uninstall),
    #   install_path (str, "" on uninstall).
    SHORTCUT_INSTALL_STATE_CHANGED = "shortcut_install_state_changed"

    # Emitted by ShortcutService once a bulk reconcile (post-sync)
    # finishes. Carries the per-batch counters so the frontend can
    # decide whether to prompt the user for a Steam restart (any
    # ``added`` > 0 or ``removed`` > 0 invalidates Steam's in-memory
    # copy of shortcuts.vdf — without a restart, Steam overwrites
    # our changes on its next shutdown). Payload fields:
    #   added (int), removed (int), kept (int), total (int)
    SHORTCUT_RECONCILE_COMPLETE = "shortcut_reconcile_complete"

    # ── UI toast notification ────────────────────────────────────
    # Generic frontend toast trigger. Emitted by any service that
    # needs to surface a user-facing message asynchronously
    # (launcher error, circuit breaker tripped, sync failed, etc.).
    # The frontend subscribes via the bus bridge and displays the
    # toast styled per ``severity``.
    # Added 2026-05-15 (lot 12c): the emit sites in
    # services/launcher/{circuit_breaker,error_toasts}.py have
    # always referenced ``Events.TOAST_NOTIFICATION`` but the enum
    # member was never declared — both call sites were silent
    # no-ops, so launcher errors and circuit-breaker trips never
    # actually reached the UI.
    # Payload fields: severity ("info" | "warning" | "error"),
    #   duration_ms (int), i18n_key (str), params (dict[str, Any]),
    #   actions (list[dict[str, Any]], opt).
    TOAST_NOTIFICATION = "toast_notification"

    # On-demand artwork fetch request. Any caller may emit this to
    # ask ArtworkService to pull covers for a given title from
    # SteamGridDB. ArtworkService deduplicates by app_id (won't
    # fetch if artwork already present unless force=True).
    # Payload fields: app_id (int), title (str), store (str, opt),
    #   game_id (str, opt), force (bool, opt, default False)
    ARTWORK_REQUEST = "artwork_request"
    # ── Cloud-save sync lifecycle ────────────────────────────────
    # Emitted by ``CloudSaveService`` to surface per-game save
    # transfer outcomes to the UI. The DOWN events fire on the
    # game→local pull (pre-launch); the UP events fire on the
    # local→cloud push (post-exit). ``COMPLETE`` carries
    # ``synced: bool`` so the UI can distinguish "ran the sync
    # but had no changes" from "skipped entirely"; ``FAILED``
    # carries an ``error`` string for the toast text.
    # Common payload fields: store (str), game_id (str).
    # COMPLETE adds: synced (bool).
    # FAILED adds: error (str).
    CLOUD_SYNC_DOWN_COMPLETE = "cloud_sync_down_complete"
    CLOUD_SYNC_DOWN_FAILED = "cloud_sync_down_failed"
    CLOUD_SYNC_UP_COMPLETE = "cloud_sync_up_complete"
    CLOUD_SYNC_UP_FAILED = "cloud_sync_up_failed"


class StoreStatus(StrEnum):
    """Store availability after a status check."""

    UNAVAILABLE = "unavailable"
    NOT_AUTHENTICATED = "not_authenticated"
    AVAILABLE = "available"
    ERROR = "error"


class StoreEnum(StrEnum):
    """Canonical store IDs used as dict keys and frontend routes."""

    EPIC = "epic"
    GOG = "gog"
    AMAZON = "amazon"
    MICROSOFT = "microsoft"
    UBISOFT = "ubisoft"
    BATTLENET = "battlenet"


class OwnershipType(StrEnum):
    """How a game is owned (full purchase vs subscription)."""

    OWNED = "owned"
    SUBSCRIBED = "subscribed"
    TRIAL = "trial"
    UNKNOWN = "unknown"


class GameTag(StrEnum):
    """Filters applied by the UI to group/hide games."""

    NATIVE = "native"
    PROTON = "proton"
    CLOUD = "cloud"
    XCLOUD = "xcloud"
    DLC = "dlc"
    BETA = "beta"
    DEMO = "demo"
    HIDDEN = "hidden"


class ErrorCode(StrEnum):
    """Normalized error codes across stores.

    Store connectors convert their raw errors (HTTP status,
    subprocess exit code, API string) into one of these values so
    the frontend can match on stable identifiers instead of
    parsing free-form messages.
    """

    NOT_AUTHENTICATED = "not_authenticated"
    TOKEN_EXPIRED = "token_expired"  # noqa: S105 — event name constant, not a credential
    NETWORK_ERROR = "network_error"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    QUOTA_EXCEEDED = "quota_exceeded"
    INSUFFICIENT_SPACE = "insufficient_space"
    BINARY_MISSING = "binary_missing"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class SubscriptionTier(StrEnum):
    """Subscription tier for stores whose catalog depends on a paid plan.

    Sprint 18e scope is Microsoft / Xbox Game Pass. The three paid
    tiers (Essential, Premium, Ultimate) are listed for forward
    compatibility — the current implementation can only discriminate
    NONE vs. any-active until real probe responses from each tier
    are captured and parsed (Sprint 18f).

    ACTIVE_UNKNOWN is the conservative bucket for "the probe responded
    200 OK but couldn't parse a tier marker". Callers treat it as
    "skip the sync" (Sprint 18e Q1 decision) to avoid showing users
    games they can't actually stream.

    The enum inherits from str so members serialize directly to JSON
    for EventBus payloads: json.dumps(SubscriptionTier.ULTIMATE)
    produces "ultimate", which is what the frontend expects.
    """

    NONE = "none"
    ESSENTIAL = "essential"
    PREMIUM = "premium"
    ULTIMATE = "ultimate"
    ACTIVE_UNKNOWN = "active_unknown"
