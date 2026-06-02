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
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { call } from "@decky/api";
import { useRPCMutation, unwrapRpcEnvelope } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useEventBus } from "../api/event-bus-client";
import { Events } from "../types/events";
import type { AuthResult, Result, StoreId, StoreStatus } from "../types/api";

type StatusMap = Partial<Record<StoreId, StoreStatus>>;

// Started by `prefetchAuthStatus()` — called inside `definePlugin`
// where Decky's RPC bridge is guaranteed ready. AuthProvider awaits
// this so statuses are available before the first QAM paint.
let _prefetchPromise: Promise<StatusMap> | null = null;
let _prefetchResolve: ((m: StatusMap) => void) | null = null;

function _parseStatuses(raw: unknown): StatusMap {
  // Unwrap the RPC envelope ({success, data}) — call() returns
  // the raw envelope, unlike useRPC which unwraps automatically.
  const data = unwrapRpcEnvelope(raw);
  const arr: unknown[] = Array.isArray(data) ? data : [];
  const map: StatusMap = {};
  for (const entry of arr) {
    if (entry && typeof entry === "object") {
      const e = entry as Record<string, unknown>;
      const id = e.store_id as StoreId | undefined;
      if (id) {
        map[id] = e.available ? "connected" : "disconnected";
      }
    }
  }
  return map;
}

/** Call inside `definePlugin` to kick off the auth status fetch before
 *  the QAM is ever opened. Safe to call multiple times — subsequent
 *  calls are no-ops. */
export function prefetchAuthStatus(): void {
  if (_prefetchPromise) return;
  _prefetchPromise = new Promise<StatusMap>((resolve) => {
    _prefetchResolve = resolve;
  });
  call<[], unknown>(rpcRoutes.checkStoreStatus)
    .then((raw) => _prefetchResolve!(_parseStatuses(raw)))
    .catch(() => _prefetchResolve!({}));
}

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

  useEffect(() => {
    let cancelled = false;
    // Seed immediately from the prefetch (started in definePlugin)
    // so the UI paints with known status without a 1-2s delay.
    if (_prefetchPromise) {
      _prefetchPromise.then((map) => {
        if (!cancelled) setStatuses(map);
      });
    }
    // Always make a fresh call to get the latest status (auth may
    // have changed since boot). The seed above prevents a flash of
    // empty/disconnected buttons while this resolves.
    call<[], unknown>(rpcRoutes.checkStoreStatus)
      .then((raw) => {
        if (!cancelled) setStatuses(_parseStatuses(raw));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

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
    loading: Object.keys(statuses).length === 0,
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
