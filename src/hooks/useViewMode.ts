/**
 * useViewMode — game-detail view-mode preference.
 *
 * Persists to localStorage (survives Steam restarts) with a
 * window-property cache for fast in-session access. Broadcasts
 * changes via a custom event so every mounted consumer stays in
 * sync without prop drilling.
 */
import { useCallback, useEffect, useState } from "react";

export type GameDetailsViewMode = "compact" | "full";

const LS_KEY = "unifideck:view-mode";
const WIN_KEY = "__unifideck_view_mode__";
const CHANGE_EVENT = "unifideck:view-mode-change";
const DEFAULT_MODE: GameDetailsViewMode = "compact";

function readMode(): GameDetailsViewMode {
  // window cache first (fast path within a session)
  const w = window as unknown as Record<string, unknown>;
  const cached = w[WIN_KEY];
  if (cached === "compact" || cached === "full") return cached;
  // fall back to localStorage (survives restarts)
  try {
    const stored = localStorage.getItem(LS_KEY);
    if (stored === "compact" || stored === "full") return stored;
  } catch {
    /* CEF may deny access */
  }
  return DEFAULT_MODE;
}

function writeMode(mode: GameDetailsViewMode): void {
  (window as unknown as Record<string, unknown>)[WIN_KEY] = mode;
  try {
    localStorage.setItem(LS_KEY, mode);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: mode }));
}

export interface UseViewModeResult {
  mode: GameDetailsViewMode;
  setMode: (mode: GameDetailsViewMode) => void;
  toggle: () => void;
}

export function useViewMode(): UseViewModeResult {
  const [mode, setModeState] = useState<GameDetailsViewMode>(readMode);

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<GameDetailsViewMode>).detail;
      if (detail) setModeState(detail);
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
