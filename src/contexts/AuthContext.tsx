/**
 * AuthContext — per-store auth status + actions.
 *
 * Exposes :
 *  - statuses: `Record<StoreId, StoreStatus>` (connected /
 *    disconnected / expired / error)
 *  - startAuth(store) — opens OAuth flow
 *  - completeAuth(store, code) — handles 2FA / code paste
 *  - logout(store) — clears stored credentials
 *  - logoutAll() — wipe every store at once
 *
 * Listens to AUTH_COMPLETE / AUTH_FAILED / LOGOUT_COMPLETE
 * to update statuses without polling.
 */
import React, {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useState,
} from "react";
import { useRPCMutation, useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useEventBus } from "../api/event-bus-client";
import { Events } from "../types/events";
import type { AuthResult, Result, StoreId, StoreStatus } from "../types/api";
import { tabManager } from "../lib/steam-bridge/tab-container";

type StatusMap = Partial<Record<StoreId, StoreStatus>>;

/** Auth context value. */
interface AuthContextValue {
  statuses: StatusMap;
  loading: boolean;
  startAuth: (store: StoreId) => Promise<AuthResult | null>;
  completeAuth: (
    store: StoreId,
    code: string,
  ) => Promise<AuthResult | null>;
  logout: (store: StoreId) => Promise<void>;
  logoutAll: () => Promise<void>;
  /** Called by useStoreAuth after AuthDispatcher reports
   *  success — bypasses EventBus race by setting status
   *  synchronously. */
  notifyConnected: (store: StoreId) => void;
}

const Ctx = createContext<AuthContextValue | null>(null);

/**
 * Provider that tracks per-store auth status.
 * Re-evaluates on `AUTH_COMPLETE`, `LOGOUT_COMPLETE`,
 * `AUTH_FAILED` and `ACCOUNT_SWITCHED` events so the
 * UI never shows a stale Connect / Disconnect badge.
 */
export const AuthProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [statuses, setStatuses] = useState<StatusMap>({});
  // Initial fetch
  const initial = useRPCQuery<[], StatusMap>(
    rpcRoutes.checkStoreStatus,
    [],
  );
  React.useEffect(() => {
    if (initial.data) {
      // The backend returns a *list* of per-store status
      // dicts (``[{store_id, available, ...}]``), but the
      // frontend uses a flat map. Convert on receipt so
      // ``statuses["epic"] === "connected"`` works.
      const raw: unknown[] = Array.isArray(initial.data)
        ? initial.data as unknown[]
        : [];
      const map: StatusMap = {};
      for (const entry of raw) {
        if (entry && typeof entry === "object") {
          const e = entry as Record<string, unknown>;
          const id = e.store_id as StoreId | undefined;
          if (id) {
            map[id] = e.available ? "connected" : "disconnected";
          }
        }
      }
      setStatuses(map);
    }
  }, [initial.data]);

  React.useEffect(() => {
    tabManager.setConnectedStores(statuses);
  }, [statuses]);

  // RPC mutations
  const startMut = useRPCMutation<
    [StoreId, "start"], AuthResult
  >(rpcRoutes.storeAuth);
  const completeMut = useRPCMutation<
    [StoreId, "complete", { code: string }], AuthResult
  >(rpcRoutes.storeAuth);
  const logoutMut = useRPCMutation<
    [StoreId, "logout"], Result
  >(rpcRoutes.storeAuth);
  const logoutAllMut = useRPCMutation<[], Result>(
    rpcRoutes.clearStoreAuths,
  );

  // Bus reactions — use the canonical STORE_* names from
  // `Events`. The legacy AUTH_COMPLETE / AUTH_FAILED /
  // LOGOUT_COMPLETE aliases never existed on the bus so the
  // old code silently never updated status.
  useEventBus(Events.STORE_AUTH_COMPLETE, (payload) => {
    const store = payload.store as StoreId | undefined;
    if (store) setStatuses((s) => ({ ...s, [store]: "connected" }));
  });

  useEventBus(Events.STORE_AUTH_FAILED, (payload) => {
    const store = payload.store as StoreId | undefined;
    if (store) setStatuses((s) => ({ ...s, [store]: "error" }));
  });

  useEventBus(Events.STORE_LOGOUT, (payload) => {
    const store = payload.store as StoreId | undefined;
    if (store) setStatuses((s) => ({ ...s, [store]: "disconnected" }));
  });

  const startAuth = useCallback(
    (store: StoreId) => startMut.mutate(store, "start"),
    [startMut],
  );

  const completeAuth = useCallback(
    (store: StoreId, code: string) =>
      completeMut.mutate(store, "complete", { code }),
    [completeMut],
  );

  const logout = useCallback(
    async (store: StoreId) => {
      await logoutMut.mutate(store, "logout");
    },
    [logoutMut],
  );

  /** Logout all. */
  const logoutAll = useCallback(async () => {
    await logoutAllMut.mutate();
    setStatuses({});
  }, [logoutAllMut]);

  const notifyConnected = useCallback(
    (store: StoreId) => {
      setStatuses((s) => ({ ...s, [store]: "connected" }));
    },
    [],
  );

  const value: AuthContextValue = {
    statuses,
    loading: initial.loading,
    startAuth, completeAuth, logout, logoutAll,
    notifyConnected,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * Access the AuthContext value. Throws if used
 * outside `<AuthProvider>`.
 *
 * @throws Error when the provider is missing.
 */
export function useAuth(): AuthContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth called outside <AuthProvider>");

  return v;
}
