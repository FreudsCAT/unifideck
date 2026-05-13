/**
 * EventBus event names — mirror of backend `core/types/events.py`.
 *
 * Names and lowercase string values match the backend Events
 * enum exactly. The frontend subscribes via `subscribe_replay`
 * (see `api/event-bus-client.ts`) ; these are the wire strings
 * the backend emits, so a typo here breaks the bridge silently.
 *
 * Single source of truth on the frontend. Components never
 * reference event strings directly — they import `Events`.
 */
export const Events = {
  // Authentication (backend prefixes auth events with STORE_)
  STORE_AUTH_STARTED: "store_auth_started",
  STORE_AUTH_COMPLETE: "store_auth_complete",
  STORE_AUTH_FAILED: "store_auth_failed",
  STORE_LOGOUT: "store_logout",
  STORE_REGISTERED: "store_registered",
  // Library sync
  SYNC_STARTED: "sync_started",
  SYNC_PROGRESS: "sync_progress",
  SYNC_COMPLETE: "sync_complete",
  SYNC_FAILED: "sync_failed",
  SYNC_CANCELLED: "sync_cancelled",
  SYNC_SKIPPED: "sync_skipped",
  // Downloads
  DOWNLOAD_QUEUED: "download_queued",
  DOWNLOAD_STARTED: "download_started",
  DOWNLOAD_PROGRESS: "download_progress",
  DOWNLOAD_COMPLETE: "download_complete",
  DOWNLOAD_FAILED: "download_failed",
  DOWNLOAD_CANCELLED: "download_cancelled",
  // Game state
  GAME_INSTALLED: "game_installed",
  GAME_UNINSTALLED: "game_uninstalled",
  GAME_UPDATE_AVAILABLE: "game_update_available",
  GAME_LAUNCHED: "game_launched",
  GAME_STOPPED: "game_stopped",
  // Errors and toasts
  STORE_ERROR: "store_error",
  LAUNCHER_STAGE: "launcher_stage",
  CIRCUIT_STATE_CHANGED: "circuit_state_changed",
} as const;

/**
 * Union of every event name the EventBus may emit. Derived
 * from the `Events` constant so adding a new event in one
 * place updates this type in lockstep.
 */
export type EventName = (typeof Events)[keyof typeof Events];

/**
 * Standard payload fields for events that carry a toast or
 * a follow-up action (e.g. LAUNCHER_STAGE on cloud sync
 * failure). The backend includes `action` when there is a
 * URI verb the frontend should expose as a button. Frontend
 * dispatches that URI via `dispatch_unifideck_action`.
 */
export interface ToastActionPayload {
  severity?: "info" | "warning" | "error";
  i18n_key?: string;
  i18n_params?: Record<string, unknown>;
  duration_ms?: number;
  action?: { verb: string; args: string[] };
}
