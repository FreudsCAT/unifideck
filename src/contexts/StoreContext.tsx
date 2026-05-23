/**
 * StoreContext — registered stores + per-store visual config.
 *
 * Loads `get_store_infos` once on mount, exposes the array
 * to the rest of the tree. Refresh is exposed as `refetch`
 * so consumers (e.g. after STORE_REGISTERED event) can
 * update without re-mounting the provider.
 *
 * Design choice : auth status lives in `AuthContext`, NOT
 * here. This context is concerned with "which stores are
 * registered" only — the connectivity status changes far
 * more often and merging the two would cause spurious
 * re-renders.
 */
import { createContext, FC, ReactNode, useContext } from "react";
import { useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
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
 * Provider that loads the registered stores once on
 * mount via the `get_store_infos` RPC and exposes
 * them to descendants. Listens to the
 * `STORE_REGISTERED` event so a runtime-registered
 * store appears without remounting the tree.
 */
export const StoreProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const { data, loading, error, refetch } = useRPCQuery<[], StoreInfo[]>(rpcRoutes.getStoreInfos,[]);
  const value: StoreContextValue = {
    stores: data ?? [],
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
