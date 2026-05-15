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
    # Payload fields: i18n_key (str), severity
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

    # Generic store error
    STORE_ERROR = "store_error"

    RUNTIME_PROBES_REPORTED = "runtime_probes_reported"

    # security audit events. Emitted by the
    # security package + token managers + auth flows. Consumed
    # by SecurityService for audit logging, counters, and
    # centralised policy enforcement.
    SECURITY_TOKEN_ENCRYPTED = "security_token_encrypted"
    SECURITY_TOKEN_DECRYPTED = "security_token_decrypted"
    SECURITY_DECRYPT_FAILED = "security_decrypt_failed"
    SECURITY_TOKEN_FILE_MIGRATED = "security_token_file_migrated"
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
    SECURITY_TOKEN_AGE_EXCEEDED = "security_token_age_exceeded"

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

    # Cross-store deduplication outcome. Emitted by SyncService
    # immediately before SYNC_COMPLETE whenever at least one duplicate
    # was removed. Lets the frontend show a toast ("3 duplicates
    # skipped across GOG / Amazon") without having to diff two library
    # snapshots. Microsoft is never in the payload — xCloud / Game
    # Pass entries don't participate in dedup.
    # Payload fields:
    #   total_removed (int)         — sum of all per-store drops
    #   per_store (dict[str, int])  — {store_name: removed_count}, only
    #                                 stores that lost at least one
    SYNC_DEDUP = "sync_dedup"

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
    TOKEN_EXPIRED = "token_expired"
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
