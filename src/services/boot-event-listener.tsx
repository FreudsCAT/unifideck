/**
 * boot-event-listener — panel-independent event → toast/modal bridge.
 *
 * Replaces the QAM-bound `<ToastEventListener>` for events that must
 * be handled regardless of whether the Quick Access panel is open:
 *
 *   - `LAUNCHER_STAGE`       → toast or CloudSaveConflictModal
 *   - `STORE_ERROR`          → error toast
 *   - `STORE_AUTH_COMPLETE`  → navigate to /library/home
 *
 * Started from `definePlugin` and torn down on `onDismount`.
 * Uses the same imperative APIs (`toaster.toast`, `showModal`,
 * `Navigation.Navigate`) that `launcherToasts.tsx` already uses
 * from non-React code, confirmed safe by Decky's `@decky/ui`
 * implementation (showModal resolves the SP window via findSP()).
 */
import { toaster } from "@decky/api";
import { showModal, Navigation } from "@decky/ui";
import i18n from "i18next";
import { EventBusClient } from "../api/event-bus-client";
import { type ToastActionPayload } from "../types/events";
import { CloudSaveConflictModal } from "../components/modals/CloudSaveConflictModal";

const DEFAULT_DURATION = 5000;
const ERROR_DURATION = 7500;

/** Show a toast via the imperative Decky toaster API. */
function showToast(
  title: string,
  body: string,
  severity?: "info" | "warning" | "error",
): void {
  const longer = severity === "error" || severity === "warning";
  try {
    toaster.toast({
      title,
      body,
      duration: longer ? ERROR_DURATION : DEFAULT_DURATION,
    });
  } catch {
    console.log(`[BootEventListener] ${title}: ${body}`);
  }
}

/**
 * Start the boot-time event listener. Returns a cleanup function
 * that unsubscribes all handlers (called from `runTeardown`).
 */
export function startBootEventListener(): () => void {
  const unsubs: (() => void)[] = [];

  // ── LAUNCHER_STAGE ────────────────────────────────────
  unsubs.push(
    EventBusClient.subscribe("launcher_stage", (payload) => {
      const p = payload as ToastActionPayload;
      // `game_title` arrives as a top-level field while the strings
      // interpolate `{{gameTitle}}`; merging it here is what stops every
      // launcher toast rendering with the placeholder unfilled.
      const params = {
        ...(p.game_title ? { gameTitle: String(p.game_title) } : {}),
        ...((p.i18n_params ?? {}) as Record<string, string>),
      } as Record<string, string>;
      const message = p.i18n_key ? String(i18n.t(p.i18n_key, params)) : "";
      if (!message) return;

      // Cloud-save conflict → modal
      if (p.action?.verb === "retry-sync") {
        const [store, gameId, phase] = p.action.args;
        showModal(
          <CloudSaveConflictModal
            gameTitle={String(
              (payload as Record<string, unknown>).game_title ?? gameId,
            )}
            local={
              ((payload as Record<string, unknown>).local_snapshot ??
                {}) as never
            }
            remote={
              ((payload as Record<string, unknown>).remote_snapshot ??
                {}) as never
            }
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

      // Generic toast
      if (p.i18n_title_key) {
        const title = String(i18n.t(p.i18n_title_key, params));
        showToast(title, message, p.severity);
      } else {
        showToast(message, "", p.severity);
      }
    }),
  );

  // ── STORE_ERROR ───────────────────────────────────────
  unsubs.push(
    EventBusClient.subscribe("store_error", (payload) => {
      const store = String(payload.store ?? "?");
      const errType = String(payload.error_type ?? "error");
      showToast(
        String(i18n.t("toasts.storeError", { store, errType })),
        "",
        "error",
      );
    }),
  );

  // ── STORE_AUTH_COMPLETE ───────────────────────────────
  unsubs.push(
    EventBusClient.subscribe("store_auth_complete", () => {
      try {
        Navigation.Navigate("/library/home");
      } catch (e) {
        console.error("[BootEventListener] post-auth navigation failed:", e);
      }
    }),
  );

  return () => {
    for (const unsub of unsubs) unsub();
  };
}
