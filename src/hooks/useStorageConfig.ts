/**
 * useStorageConfig — storage-location actions hook.
 *
 * Wraps the storage-related RPCs (`get_storage_locations`,
 * `set_default_storage_location`, `set_custom_install_path`)
 * behind a single typed surface so settings components stay
 * presentational (no `call()` in the component layer).
 *
 * Cache invariant : the locations list is fetched once on
 * mount and refreshed on every mutation so the default badge
 * is always live.
 */
import { useCallback } from "react";
import { useRPC, useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import type {
  StorageLocation,
  StorageLocationsResponse,
} from "../types/downloads";

/**
 * Aggregated result returned by {@link useStorageConfig} —
 * the current list of locations + the default selection +
 * mutators that refresh the list automatically.
 */
export interface UseStorageConfigResult {
  locations: StorageLocationsResponse["locations"];
  defaultLocation: StorageLocation;
  loading: boolean;
  refresh: () => Promise<void>;
  setDefault: (id: StorageLocation) => Promise<boolean>;
  setCustomPath: (path: string) => Promise<boolean>;
}

/** Storage-location query + mutators in one hook. Components
 *  call this instead of issuing their own RPCs so a backend
 *  rename is a one-file change. */
export function useStorageConfig(): UseStorageConfigResult {
  const query = useRPCQuery<[], StorageLocationsResponse>(
    rpcRoutes.getStorageLocations,
    [],
  );
  const setDefaultRPC = useRPC<[StorageLocation], { success: boolean }>(
    rpcRoutes.setDefaultStorageLocation,
  );
  const setCustomRPC = useRPC<[string], { success: boolean }>(
    rpcRoutes.setCustomInstallPath,
  );

  const refresh = useCallback(async (): Promise<void> => {
    await query.refetch();
  }, [query]);

  const setDefault = useCallback(
    async (id: StorageLocation): Promise<boolean> => {
      const r = await setDefaultRPC(id);
      if (r?.success) await refresh();
      return Boolean(r?.success);
    },
    [setDefaultRPC, refresh],
  );

  const setCustomPath = useCallback(
    async (path: string): Promise<boolean> => {
      const r = await setCustomRPC(path);
      if (r?.success) await refresh();
      return Boolean(r?.success);
    },
    [setCustomRPC, refresh],
  );

  return {
    locations: query.data?.locations ?? [],
    defaultLocation: query.data?.default ?? "internal",
    loading: query.loading,
    refresh,
    setDefault,
    setCustomPath,
  };
}
