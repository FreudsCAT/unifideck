# OP-05a | core/types/events.py | Depends: (none)
from __future__ import annotations

from enum import StrEnum


class Events(StrEnum):
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADING = "plugin_unloading"

    SYNC_STARTED = "sync_started"
    SYNC_PROGRESS = "sync_progress"
    SYNC_COMPLETE = "sync_complete"
    SYNC_FAILED = "sync_failed"
    SYNC_CANCELLED = "sync_cancelled"

    STORE_AUTH_STARTED = "store_auth_started"
    STORE_AUTH_COMPLETE = "store_auth_complete"
    STORE_AUTH_FAILED = "store_auth_failed"
    STORE_LOGOUT = "store_logout"

    STORE_REGISTERED = "store_registered"

    GAME_INSTALLED = "game_installed"
    GAME_UNINSTALLED = "game_uninstalled"
    GAME_UPDATE_AVAILABLE = "game_update_available"
    GAME_LAUNCHED = "game_launched"
    GAME_STOPPED = "game_stopped"

    LAUNCHER_STAGE = "launcher_stage"

    CIRCUIT_STATE_CHANGED = "circuit_state_changed"

    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOAD_STARTED = "download_started"
    DOWNLOAD_PROGRESS = "download_progress"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_CANCELLED = "download_cancelled"

    STORE_ERROR = "store_error"

    RUNTIME_PROBES_REPORTED = "runtime_probes_reported"

    SECURITY_TOKEN_ENCRYPTED = "security_token_encrypted"
    SECURITY_TOKEN_DECRYPTED = "security_token_decrypted"
    SECURITY_DECRYPT_FAILED = "security_decrypt_failed"
    SECURITY_TOKEN_FILE_MIGRATED = "security_token_file_migrated"
    SECURITY_LEGACY_PLAINTEXT_DETECTED = "security_legacy_plaintext_detected"
    SECURITY_AUTH_FLOW_STARTED = "security_auth_flow_started"
    SECURITY_AUTH_FLOW_COMPLETED = "security_auth_flow_completed"
    SECURITY_AUTH_FLOW_FAILED = "security_auth_flow_failed"

    SECURITY_PERMISSIONS_CHECK = "security_permissions_check"
    SECURITY_PERMISSIONS_REPAIRED = "security_permissions_repaired"
    SECURITY_BRUTEFORCE_SUSPECTED = "security_bruteforce_suspected"
    SECURITY_DEVICE_RESET_DETECTED = "security_device_reset_detected"
    SECURITY_FINGERPRINT_INITIALIZED = "security_fingerprint_initialized"

    SECURITY_EXTERNAL_AUTH_CHECK_FAILED = "security_external_auth_check_failed"

    CONFIG_VALIDATION_COMPLETED = "config_validation_completed"
    CONFIG_VALIDATION_FAILED = "config_validation_failed"

    SUBSCRIPTION_DETECTED = "subscription_detected"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    SUBSCRIPTION_CHECK_FAILED = "subscription_check_failed"

    SYNC_SKIPPED = "sync_skipped"

    ACCOUNT_SWITCHED = "account_switched"

    SHORTCUT_CREATED = "shortcut_created"

    ARTWORK_REQUEST = "artwork_request"


class StoreStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    NOT_AUTHENTICATED = "not_authenticated"
    AVAILABLE = "available"
    ERROR = "error"


class StoreEnum(StrEnum):
    EPIC = "epic"
    GOG = "gog"
    AMAZON = "amazon"
    MICROSOFT = "microsoft"
    UBISOFT = "ubisoft"


class OwnershipType(StrEnum):
    OWNED = "owned"
    SUBSCRIBED = "subscribed"
    TRIAL = "trial"
    UNKNOWN = "unknown"


class GameTag(StrEnum):
    NATIVE = "native"
    PROTON = "proton"
    CLOUD = "cloud"
    XCLOUD = "xcloud"
    DLC = "dlc"
    BETA = "beta"
    DEMO = "demo"
    HIDDEN = "hidden"


class ErrorCode(StrEnum):
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
    NONE = "none"
    ESSENTIAL = "essential"
    PREMIUM = "premium"
    ULTIMATE = "ultimate"
    ACTIVE_UNKNOWN = "active_unknown"
