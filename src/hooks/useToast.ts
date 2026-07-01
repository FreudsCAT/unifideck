/**
 * useToast — typed wrapper around Decky's `toaster.toast`.
 *
 * Wraps the global toaster with three semantics-bearing
 * methods (`info`, `success`, `error`) and a base `show` for
 * full control. Components import this hook instead of
 * `toaster` directly so :
 *  - duration defaults are centralised (no scattered "5000")
 *  - a future swap to a different notification system is a
 *    one-file change
 *  - a no-op fallback is provided when the @decky/api
 *    toaster is unavailable (test environments)
 */
import { toaster } from "@decky/api";
import { useCallback } from "react";

interface ToastOptions {
  duration?: number;
  body?: string;
  onClick?: () => void;
}

const DEFAULT_DURATION = 5000;
const ERROR_DURATION = 7500;

/**
 * Shape returned by {@link useToast}. The single field is
 * a stable `showToast(args)` callback — no other state.
 */
export interface UseToastResult {
  show: (title: string, body?: string, options?: ToastOptions) => void;
  info: (title: string, body?: string) => void;
  success: (title: string, body?: string) => void;
  error: (title: string, body?: string) => void;
}

/**
 * Hook returning a memoised `showToast` callback that
 * forwards to Decky Loader's toast API. Centralised
 * here so styling / duration defaults stay consistent
 * across the codebase and so test code can mock a
 * single boundary.
 */
export function useToast(): UseToastResult {
  const show = useCallback(
    (title: string, body?: string, options: ToastOptions = {}) => {
      try {
        toaster.toast({
          title,
          body: body ?? options.body ?? "",
          duration: options.duration ?? DEFAULT_DURATION,
          onClick: options.onClick,
        });
      } catch {
        console.log(`[Toast] ${title}: ${body ?? ""}`);
      }
    },
    [],
  );

  const info = useCallback(
    (title: string, body?: string) => show(title, body),
    [show],
  );

  const success = useCallback(
    (title: string, body?: string) => show(title, body),
    [show],
  );

  const error = useCallback(
    (title: string, body?: string) =>
      show(title, body, { duration: ERROR_DURATION }),
    [show],
  );

  return { show, info, success, error };
}
