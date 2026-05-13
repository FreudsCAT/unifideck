/**
 * useViewMode — game-detail view-mode preference.
 *
 * Persists the user's choice (compact / full) to a window-
 * scoped key (`window.unifideck_view_mode`) and broadcasts
 * changes via a custom event so every mounted consumer
 * stays in sync without prop drilling. Replaces the
 * `getStoredViewMode` / `setStoredViewMode` helpers + the
 * `VIEW_MODE_CHANGE_EVENT` global from the old index.tsx.
 *
 * Persistence target deliberately NOT localStorage : the
 * Steam CEF environment doesn't reliably surface
 * localStorage to plugins. Window properties survive within
 * a Steam session, which is what we need.
 */
import { useCallback, useEffect, useState } from "react";
/**
 * Compact (cover-only) vs full (cover + metadata + scores)
 * rendering of the game-details view. Persisted user choice.
 */
export type GameDetailsViewMode = "compact" | "full";

const STORAGE_KEY = "__unifideck_view_mode__";
const CHANGE_EVENT = "unifideck:view-mode-change";
const DEFAULT_MODE: GameDetailsViewMode = "compact";

interface WindowWithViewMode extends Window {
  [STORAGE_KEY]?: GameDetailsViewMode;
}

function readMode(): GameDetailsViewMode {
  const w = window as WindowWithViewMode;
  return w[STORAGE_KEY] ?? DEFAULT_MODE;
}

function writeMode(mode: GameDetailsViewMode): void {
  const w = window as WindowWithViewMode;
  w[STORAGE_KEY] = mode;
  window.dispatchEvent(
    new CustomEvent(CHANGE_EVENT, { detail: mode }),
  );
}

/**
 * Shape returned by {@link useViewMode}. The setter writes
 * through to backend storage so the choice survives reloads.
 */
export interface UseViewModeResult {
  mode: GameDetailsViewMode;
  setMode: (mode: GameDetailsViewMode) => void;
  toggle: () => void;
}

/**
 * Hook exposing the user's preferred game-details
 * view (compact vs detailed) with persistence to
 * the backend config so the choice survives reloads
 * and Steam restarts.
 *
 * @returns current view + `setMode` setter that
 *   writes through to backend storage.
 */
export function useViewMode(): UseViewModeResult {
  const [mode, setModeState] = useState<GameDetailsViewMode>(readMode);
  // Sync this consumer when ANY other consumer changes the mode
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<GameDetailsViewMode>;
      if (ce.detail) setModeState(ce.detail);
    };
    window.addEventListener(CHANGE_EVENT, handler);

    return () => window.removeEventListener(CHANGE_EVENT, handler);
  }, []);

  const setMode = useCallback((next: GameDetailsViewMode) => {
    writeMode(next);
    setModeState(next);
  }, []);

  const toggle = useCallback(() => {
    setMode(mode === "compact" ? "full" : "compact");
  }, [mode, setMode]);

  return { mode, setMode, toggle };
}
