/**
 * launcherToasts — persistent launcher-toast poll.
 *
 * The game launcher runs as a separate process and appends its
 * LAUNCHER_STAGE toast events to a shared bridge file (backend
 * `launcher.frontend_bridge`). This poll drains them via the
 * `get_launcher_toasts` RPC and surfaces them.
 *
 * It is started from `definePlugin` and runs independently of the QAM
 * panel, because launches happen with the Unifideck panel CLOSED in
 * Gaming Mode — and the QAM-bound `ToastEventListener` only polls while
 * the panel is open, so it never sees launch-time toasts (first-time
 * prefix setup, dependency install, Proton switch, …). This poll is the
 * panel-independent delivery path for everything the launcher emits.
 */
import { call, toaster } from "@decky/api";
import { showModal } from "@decky/ui";
import i18n from "i18next";
import { rpcRoutes } from "../api/rpc-routes";
import { unwrapRpcEnvelope } from "../api/useRPC";
import { EventBusClient } from "../api/event-bus-client";
import { CloudSaveConflictModal } from "../components/modals/CloudSaveConflictModal";

const POLL_INTERVAL_MS = 2000;
const DEFAULT_DURATION = 5000;
const LONG_DURATION = 7500;

interface LauncherToast {
  i18n_key?: string;
  i18n_title_key?: string;
  i18n_params?: Record<string, unknown>;
  severity?: "info" | "warning" | "error";
  game_title?: string;
  action?: { verb: string; args: string[] };
  local_snapshot?: Record<string, unknown>;
  remote_snapshot?: Record<string, unknown>;
}

/**
 * Start the launcher-toast poll. Returns a stop function for teardown.
 */
export function startLauncherToastPoll(): () => void {
  let stopped = false;

  const tick = async (): Promise<void> => {
    let events: LauncherToast[];
    try {
      const raw = await call<[], unknown>(rpcRoutes.getLauncherToasts);
      events = unwrapRpcEnvelope<LauncherToast[]>(raw, {
        route: rpcRoutes.getLauncherToasts,
        throwing: false,
      });
    } catch {
      return; // best-effort — launcher toasts are non-critical
    }
    if (!Array.isArray(events)) return;
    for (const ev of events) showLauncherToast(ev);
  };

  const timer = setInterval(() => {
    if (!stopped) void tick();
  }, POLL_INTERVAL_MS);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

function showLauncherToast(ev: LauncherToast): void {
  // `game_title` is a top-level payload field, but the strings interpolate
  // it as `{{gameTitle}}` — so without merging it in here every launcher
  // toast rendered with the placeholder unfilled ("Starting  through
  // Battle.net…"). An explicit i18n_params entry still wins, since a caller
  // that named the variable meant it.
  const params = {
    ...(ev.game_title ? { gameTitle: ev.game_title } : {}),
    ...((ev.i18n_params ?? {}) as Record<string, string>),
  } as Record<string, string>;

  // Cloud-save conflict → modal so the user can pick keep-local/remote.
  if (ev.action?.verb === "retry-sync") {
    const [store, gameId, phase] = ev.action.args;
    showModal(
      <CloudSaveConflictModal
        gameTitle={String(ev.game_title ?? gameId)}
        local={(ev.local_snapshot ?? {}) as never}
        remote={(ev.remote_snapshot ?? {}) as never}
        onKeepLocal={() => {
          void EventBusClient.dispatchAction(
            "retry-sync",
            store,
            gameId,
            "sync_up",
          );
        }}
        onKeepRemote={() => {
          void EventBusClient.dispatchAction(
            "retry-sync",
            store,
            gameId,
            phase,
          );
        }}
        onCancel={() => {}}
        closeModal={() => {}}
      />,
    );
    return;
  }

  const message = ev.i18n_key ? String(i18n.t(ev.i18n_key, params)) : "";
  if (!message && !ev.i18n_title_key) return;

  // Match the QAM ToastEventListener: title key (when present) is the
  // bold header and i18n_key is the body; otherwise the message is the
  // title with no body.
  const title = ev.i18n_title_key
    ? String(i18n.t(ev.i18n_title_key, params))
    : message;
  const body = ev.i18n_title_key ? message : "";
  const longer = ev.severity === "error" || ev.severity === "warning";

  try {
    toaster.toast({
      title,
      body,
      duration: longer ? LONG_DURATION : DEFAULT_DURATION,
    });
  } catch {
    console.log(`[LauncherToast] ${title}: ${body}`);
  }
}
