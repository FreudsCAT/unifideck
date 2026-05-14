/**
 * useStoreAuth — high-level auth flow per store.
 *
 * Thin React adapter over the orchestration layer. The actual
 * multi-step handshake (backend prep → Steam shortcut launch
 * → wait for `STORE_AUTH_COMPLETE`) lives in
 * `services/auth/AuthDispatcher.ts` per the PDF spec : hooks
 * shouldn't own multi-stage coordination.
 *
 * Returned shape :
 *  - `info`        : StoreInfo for the requested store
 *  - `status`      : current StoreStatus
 *  - `connect`     : start auth, await terminal event
 *  - `disconnect`  : logout + clear local status
 *  - `submit2FA`   : complete an in-flight auth with a code
 *  - `busy`        : true when an auth call is in flight
 */
import { useCallback, useState } from "react";
import { showModal } from "@decky/ui";
import { useAuth } from "../contexts/AuthContext";
import { useStores } from "../contexts/StoreContext";
import { useToast } from "./useToast";
import { AuthDispatcher } from "../services/auth/AuthDispatcher";
import { ChromiumInstallModal } from "../components/modals/ChromiumInstallModal";
import type { AuthResult, StoreId } from "../types/api";

/**
 * Shape returned by {@link useStoreAuth}. Bundles the
 * reactive `status` field with the action callbacks so
 * components destructure once instead of subscribing to
 * three hooks.
 */
export interface UseStoreAuthResult {
  info: ReturnType<typeof useStores>["stores"][number] | null;
  status: ReturnType<typeof useAuth>["statuses"][StoreId];
  busy: boolean;
  connect: () => Promise<AuthResult | null>;
  disconnect: () => Promise<void>;
  submit2FA: (code: string) => Promise<AuthResult | null>;
}

/**
 * Hook that drives per-store auth flows. Delegates the
 * full handshake to {@link AuthDispatcher} and surfaces
 * the result as toasts. Status is reactive : auth events
 * trigger a re-render in `AuthContext` so the UI flips
 * Connect → Connected without polling.
 *
 * @param storeId — id of the store to drive.
 * @returns auth state + action callbacks.
 */
export function useStoreAuth(store: StoreId): UseStoreAuthResult {
  const auth = useAuth();
  const { stores } = useStores();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const info = stores.find((s) => s.name === store) ?? null;
  const status = auth.statuses[store];

  const connect = useCallback(async (): Promise<AuthResult | null> => {
    setBusy(true);
    try {
      toast.info(`Starting ${store} sign-in…`);
      const result = await AuthDispatcher.start(store);
      // Browser-based OAuth needs Microsoft Edge. When the
      // backend reports the prereq is missing, surface a
      // modal with an Install button rather than a useless
      // toast. The modal retries the auth flow on success.
      if (!result.success && result.error === "edge_not_installed") {
        showModal(
          <ChromiumInstallModal
            onInstalled={() => {
              // Retry the auth flow now that Edge is in.
              void connect();
            }}
            closeModal={() => {}}
          />,
        );
        return result;
      }
      if (result.success) {
        toast.success(`${store} connected`);
      } else {
        toast.error(`${store} sign-in failed`, result.error);
      }
      return result;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`${store} sign-in failed`, message);
      return { success: false, store, error: message };
    } finally {
      setBusy(false);
    }
  }, [store, toast]);

  const disconnect = useCallback(async () => {
    setBusy(true);
    try {
      await auth.logout(store);
    } finally {
      setBusy(false);
    }
  }, [auth, store]);

  const submit2FA = useCallback(
    async (code: string) => {
      setBusy(true);
      try {
        return await auth.completeAuth(store, code);
      } finally {
        setBusy(false);
      }
    },
    [auth, store],
  );

  return { info, status, busy, connect, disconnect, submit2FA };
}
