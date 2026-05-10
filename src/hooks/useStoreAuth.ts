/**
 * useStoreAuth — high-level auth flow per store.
 *
 * Glues `AuthContext` (status + actions) and `StoreContext`
 * (registered stores + visuals) for components that drive
 * the auth UX. Returned shape :
 *  - `info`     : StoreInfo for the requested store (name,
 *                 display_name, brand color, icon path)
 *  - `status`   : current StoreStatus (connected / ...)
 *  - `connect`  : kick off auth (returns AuthResult or null)
 *  - `disconnect` : logout + clear local status
 *  - `submit2FA`: complete an in-flight auth with a code
 *  - `busy`     : true when an auth call is in flight
 *
 * Components don't compose AuthContext + StoreContext
 * manually; this hook hides the wiring.
 */
import { useCallback, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { useStores } from "../contexts/StoreContext";
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
 * Hook that drives per-store auth flows. Wraps the
 * generic `store_auth` RPC and exposes
 * `start` / `complete` / `logout` callbacks scoped
 * to the store id provided.
 *
 * The returned `status` is reactive : auth events
 * trigger a re-evaluation of `is_available()` so
 * the UI reflects the latest state without
 * polling.
 *
 * @param storeId — id of the store to drive.
 * @returns auth state + action callbacks.
 */
export function useStoreAuth(store: StoreId): UseStoreAuthResult {
  const auth = useAuth();
  const { stores } = useStores();
  const [busy, setBusy] = useState(false);
  const info = stores.find((s) => s.name === store) ?? null;
  const status = auth.statuses[store];

  const connect = useCallback(async () => {
    setBusy(true);
    try {
      return await auth.startAuth(store);
    } finally {
      setBusy(false);
    }
  }, [auth, store]);

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
