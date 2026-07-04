/**
 * useCollectionsEnabled — opt-in for `[Unifideck]` Steam Collections.
 *
 * Thin React wrapper over the single source of truth in
 * `collection-manager` (localStorage + a broadcast event). The running
 * collection manager listens to the same event, so flipping this toggle
 * creates / deletes the collections live without a Steam restart.
 */
import { useCallback, useEffect, useState } from "react";
import {
  COLLECTIONS_ENABLED_EVENT,
  isCollectionsEnabled,
  setCollectionsEnabled,
} from "../lib/steam-bridge/collection-manager";

export interface UseCollectionsEnabledResult {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
}

export function useCollectionsEnabled(): UseCollectionsEnabledResult {
  const [enabled, setEnabledState] = useState<boolean>(isCollectionsEnabled);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<boolean>).detail;
      if (typeof detail === "boolean") setEnabledState(detail);
    };
    window.addEventListener(COLLECTIONS_ENABLED_EVENT, handler);
    return () => window.removeEventListener(COLLECTIONS_ENABLED_EVENT, handler);
  }, []);

  const setEnabled = useCallback((next: boolean) => {
    setCollectionsEnabled(next);
    setEnabledState(next);
  }, []);

  return { enabled, setEnabled };
}
