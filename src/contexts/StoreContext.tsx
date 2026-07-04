/**
 * StoreContext — React wrapper around the boot-time StoreInfoStore.
 *
 * The `get_store_infos` RPC fetch and `STORE_REGISTERED` event
 * subscription now live in `stores/store-info-store.ts` and run
 * independently of QAM mount.
 *
 * This context provides the same API as before: `stores`, `loading`,
 * `error`, `refetch` — but state comes from `useSyncExternalStore`.
 */
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useSyncExternalStore,
} from "react";
import { storeInfoStore } from "../stores/store-info-store";
import type { StoreInfo } from "../types/api";

/** Store context value. */
interface StoreContextValue {
  stores: StoreInfo[];
  loading: boolean;
  error: Error | null;
  refetch: () => Promise<void>;
}

const Ctx = createContext<StoreContextValue | null>(null);

/**
 * Provider that exposes registered stores to descendants.
 * State comes from the boot-time `storeInfoStore` singleton.
 */
export const StoreProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const { stores, loading, error } = useSyncExternalStore(
    storeInfoStore.subscribe,
    storeInfoStore.getSnapshot,
  );

  const refetch = useCallback(async () => {
    await storeInfoStore.refetch();
  }, []);

  const value: StoreContextValue = {
    stores,
    loading,
    error,
    refetch,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * Access the StoreContext value. Throws if called
 * outside the provider — that always indicates a
 * bug in the component tree, never a recoverable
 * runtime condition.
 *
 * @throws Error when the surrounding tree is missing
 *   `<StoreProvider>`.
 */
export function useStores(): StoreContextValue {
  const v = useContext(Ctx);
  if (!v) {
    throw new Error("useStores called outside <StoreProvider>");
  }
  return v;
}
