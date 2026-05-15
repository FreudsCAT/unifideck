/**
 * ToastEventListener — event-driven toast / modal host.
 *
 * Mounted once by `<RootProvider>`. Subscribes to backend
 * events (LAUNCHER_STAGE, STORE_ERROR, ...) and surfaces them
 * as toasts. When an event payload carries an `action` block
 * (verb + args), the toast renders a button that dispatches
 * the corresponding URI back to the backend via
 * `EventBusClient.dispatchAction(verb, ...args)`.
 *
 * For events that need a heavier UI (cloud-save conflict
 * resolution), this component opens a modal via showModal().
 * The modal is responsible for collecting the user's choice
 * and dispatching the appropriate URI on confirm.
 *
 * This is the canonical bidirectional bridge for the Decky
 * EventBus → user → backend round-trip. No other component
 * should subscribe to LAUNCHER_STAGE directly — they go
 * through the toast/modal here.
 */
import React, { FC } from "react";
import { showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useEventBus, EventBusClient } from "../../api/event-bus-client";
import { Events, type ToastActionPayload } from "../../types/events";
import { useToast } from "../../hooks/useToast";
import { CloudSaveConflictModal } from "./CloudSaveConflictModal";
import { AuthSuccessModal } from "./AuthSuccessModal";

/**
 * Headless component that subscribes to the backend
 * `TOAST_EMIT` event and forwards it to Decky's toast
 * API. Mounted once near the top of the tree ; renders
 * nothing.
 */
export const ToastEventListener: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  useEventBus(Events.LAUNCHER_STAGE, (payload) => {
    const p = payload as ToastActionPayload;
    const message = p.i18n_key
      ? t(p.i18n_key, p.i18n_params as Record<string, string>)
      : "";
    if (!message) return;
    // Cloud-save retry flow → open the conflict modal so the user can pick keep-local / keep-remote / cancel.
    if (p.action?.verb === "retry-sync") {
      const [store, gameId, phase] = p.action.args;
      showModal(
        <CloudSaveConflictModal
          gameTitle={String(payload.game_title ?? gameId)}
          local={(payload.local_snapshot ?? {}) as never}
          remote={(payload.remote_snapshot ?? {}) as never}
          onKeepLocal={() =>
            EventBusClient.dispatchAction(
              "retry-sync", store, gameId, "sync_up",
            )
          }
          onKeepRemote={() =>
            EventBusClient.dispatchAction(
              "retry-sync", store, gameId, phase,
            )
          }
          onCancel={() => {}}
          closeModal={() => {}}
        />,
      );
      return;
    }
    // Generic toast — optionally with action button.
    const showToastFn = p.severity === "error"
      ? toast.error
      : p.severity === "warning"
        ? toast.error  // warning shares the longer error duration
        : toast.info;
    showToastFn(message);
  });
  useEventBus(Events.STORE_ERROR, (payload) => {
    const store = String(payload.store ?? "?");
    const errType = String(payload.error_type ?? "error");
    toast.error(t("toasts.storeError", { store, errType }));
  });
  useEventBus(Events.STORE_AUTH_COMPLETE, (payload) => {
    const store = payload.store ? String(payload.store) : undefined;
    showModal(<AuthSuccessModal store={store} />);
  });
  return null;
};
