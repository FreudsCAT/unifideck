/**
 * Clipboard helper for the Steam CEF runtime.
 *
 * Steam's own UI always writes text through the *owning window's*
 * navigator (`<window>.navigator.clipboard.writeText(...)`), never the
 * ambient `navigator` — its Remote Play invite link, controller mapping
 * export and bug-report copy all do this. The reason matters here:
 * Gaming Mode renders the Quick Access panel into its own popup window,
 * and Chromium rejects `writeText` with `NotAllowedError` when the
 * document performing the write is not the focused one. Callers pass the
 * window that owns the element they were activated from.
 *
 * Lives in `utils/` rather than inside the component because components
 * must not reach for browser globals directly.
 *
 * Returns `false` instead of throwing — a failed copy is a UI nicety,
 * and the caller decides whether it is worth a toast.
 */
export async function copyTextToClipboard(
  text: string,
  win: Window | null,
): Promise<boolean> {
  const nav = (win ?? window).navigator;
  if (typeof nav?.clipboard?.writeText !== "function") return false;
  try {
    await nav.clipboard.writeText(text);
    return true;
  } catch (e) {
    console.warn("[Clipboard] writeText failed", e);
    return false;
  }
}
