/**
 * Play-section focus styling + shared layout utilities.
 *
 * Single CSS payload injected once into <head>. Provides:
 *  - Brand-coloured gradient backgrounds for the five action
 *    buttons (`install`, `play`, `resume`, `stop`, `cancel`,
 *    `update`) on hover / gamepad-focus (`.gpfocus`) / active.
 *  - A `unifideck-slide` keyframe used by the indeterminate
 *    progress bar (extracting / verifying phases).
 *  - Layout helpers (`unifideck-play-row`, `unifideck-meta-strip`,
 *    `unifideck-icon-col`) so each variant component stays
 *    presentation-light.
 *
 * Class-scoped — does not touch any element Unifideck doesn't
 * render itself.
 */

import {
  playSectionClasses,
  basicAppDetailsSectionStylerClasses,
} from "@decky/ui";

const STYLE_ID = "unifideck-play-focus-styles";

/** Marker class added to the App-Details inner container (in
 *  `AppDetailsPatch`) for non-Steam shortcuts. {@link nativeAppDetailsHideCss}
 *  scopes the native-section hides to subtrees carrying it, so Steam
 *  games (never marked) keep their native UI. */
export const HIDE_NATIVE_PLAY_MARKER = "unifideck-hide-native-play";

/**
 * CSS that hides Steam's native App-Details sections we replace for
 * shortcut pages: the native Play row AND the lower tabbed section
 * (Activity / Your Stuff / Community / Game Info — i.e. the whole
 * `AppDetailsContainer`, nav row + tab content).
 *
 * Steam ships *separate* app-details modules for desktop and Gaming
 * Mode (BPM / gamepadui), each with its own obfuscated class for the
 * native Play row:
 *   - Gaming Mode → `basicAppDetailsSectionStylerClasses.PlaySection`
 *     (the module `@decky/ui` matches on `AppDetailsRoot`)
 *   - Desktop     → `playSectionClasses.Container`
 * The tabbed section is `basicAppDetailsSectionStylerClasses
 * .AppDetailsContainer`. All rotate per Steam build, so they're
 * resolved at runtime. These sections render in a *different* subtree
 * from our injected wrapper (so they can't be removed in-tree);
 * instead the patch marks a common ancestor with
 * {@link HIDE_NATIVE_PLAY_MARKER} and these rules hide the targets
 * beneath it. Purely declarative — applies the instant they paint, no
 * RPC / CDP round-trip. We emit a rule for every class we can resolve
 * so the hide lands in whichever UI is rendering. Returns `""` (no-op)
 * if none resolve.
 *
 * Render it inline via `<style>` in our injected subtree (same pattern
 * as {@link PLAY_FOCUS_CSS} / `PlayShell`) so it lands in the right
 * CEF document.
 */
export function nativeAppDetailsHideCss(): string {
  const classes = new Set<string>();
  const add = (v: unknown): void => {
    if (typeof v === "string" && v) classes.add(v);
  };
  // Native Play row (Gaming Mode + desktop variants).
  add(basicAppDetailsSectionStylerClasses?.PlaySection);
  add(playSectionClasses?.Container);
  // Lower tabbed section (Activity / Community / Game Info + content).
  add(basicAppDetailsSectionStylerClasses?.AppDetailsContainer);
  return Array.from(classes)
    .map(
      (c) =>
        `.${HIDE_NATIVE_PLAY_MARKER} [class*="${c}"]{display:none !important;}`,
    )
    .join("\n");
}

/** Focus / hover styling for every Unifideck-rendered button.
 *  Exported so components can render it inline via `<style>` in
 *  their own subtree — the reliable way to get the rules into the
 *  right CEF document (the App-Details patch and the QuickAccess
 *  panel render in SEPARATE documents, and a `document.head`
 *  injection from one doesn't reach the other). Matches staging's
 *  ``<style>{buttonStyles}</style>`` pattern. */
export const PLAY_FOCUS_CSS = `
.unifideck-install-btn,
.unifideck-play-btn,
.unifideck-resume-btn,
.unifideck-stop-btn,
.unifideck-cancel-btn,
.unifideck-update-btn,
.unifideck-icon-btn {
  background: rgba(255, 255, 255, 0.10) !important;
  transition: background 0.15s ease, filter 0.15s ease, transform 0.1s ease !important;
}

.unifideck-install-btn { min-width: 200px; height: 48px; }
.unifideck-play-btn,
.unifideck-resume-btn,
.unifideck-update-btn { min-width: 200px; height: 48px; }
.unifideck-stop-btn,
.unifideck-icon-btn { width: 48px; height: 48px; padding: 0 !important; }
.unifideck-cancel-btn { min-width: 160px; height: 44px; }

/* Focus accent matches Steam: Install / Resume blue, Play green,
 * Cancel red, Update amber. We match three focus signals so the
 * colour lands no matter where Steam attaches the focus marker:
 * the gamepad gpfocus class, native :focus, and :focus-within
 * (when the focusable node is a child of the element carrying our
 * class). */
.unifideck-install-btn:hover,
.unifideck-install-btn:focus,
.unifideck-install-btn:focus-within,
.unifideck-install-btn.gpfocus {
  background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
  box-shadow: 0 0 0 2px rgba(26, 159, 255, 0.55) inset !important;
}
.unifideck-cancel-btn:hover,
.unifideck-cancel-btn:focus,
.unifideck-cancel-btn:focus-within,
.unifideck-cancel-btn.gpfocus {
  background: linear-gradient(135deg, #dc3545 0%, #c82333 100%) !important;
  box-shadow: 0 0 0 2px rgba(220, 53, 69, 0.55) inset !important;
}
.unifideck-play-btn:hover,
.unifideck-play-btn:focus,
.unifideck-play-btn:focus-within,
.unifideck-play-btn.gpfocus {
  background: linear-gradient(135deg, #59bf40 0%, #459e31 100%) !important;
  box-shadow: 0 0 0 2px rgba(89, 191, 64, 0.55) inset !important;
}
.unifideck-resume-btn:hover,
.unifideck-resume-btn:focus,
.unifideck-resume-btn:focus-within,
.unifideck-resume-btn.gpfocus {
  background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
  box-shadow: 0 0 0 2px rgba(26, 159, 255, 0.55) inset !important;
}
.unifideck-stop-btn:hover,
.unifideck-stop-btn:focus,
.unifideck-stop-btn:focus-within,
.unifideck-stop-btn.gpfocus,
.unifideck-icon-btn:hover,
.unifideck-icon-btn:focus,
.unifideck-icon-btn:focus-within,
.unifideck-icon-btn.gpfocus {
  background: #ffffff !important;
  color: #1a1a1a !important;
}
.unifideck-stop-btn:hover svg,
.unifideck-stop-btn:focus svg,
.unifideck-stop-btn:focus-within svg,
.unifideck-stop-btn.gpfocus svg,
.unifideck-icon-btn:hover svg,
.unifideck-icon-btn:focus svg,
.unifideck-icon-btn:focus-within svg,
.unifideck-icon-btn.gpfocus svg {
  color: #1a1a1a !important;
  fill: #1a1a1a !important;
}
.unifideck-update-btn:hover,
.unifideck-update-btn:focus,
.unifideck-update-btn:focus-within,
.unifideck-update-btn.gpfocus {
  background: linear-gradient(135deg, #f4b400 0%, #d09100 100%) !important;
  box-shadow: 0 0 0 2px rgba(244, 180, 0, 0.55) inset !important;
}

.unifideck-install-btn:active,
.unifideck-cancel-btn:active,
.unifideck-play-btn:active,
.unifideck-resume-btn:active,
.unifideck-stop-btn:active,
.unifideck-update-btn:active,
.unifideck-icon-btn:active {
  filter: brightness(0.85) !important;
  transform: translateY(1px);
}

/* Two-column row : action(s) on the left, icons on the right. */
.unifideck-play-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  background: rgba(14, 20, 27, 0.33);
  border-radius: 6px;
}
.unifideck-action-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  flex: 1 1 auto;
}
.unifideck-button-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.unifideck-icon-col {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

/* Metadata side-rail (Space Required, Last Played). */
.unifideck-meta-strip {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.65);
  letter-spacing: 0.02em;
}
.unifideck-meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.unifideck-meta-label {
  text-transform: uppercase;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.45);
}
.unifideck-meta-value {
  color: rgba(255, 255, 255, 0.85);
  font-weight: 500;
}

/* Progress bar. */
.unifideck-progress-track {
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 2px;
  overflow: hidden;
  position: relative;
}
.unifideck-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #1a9fff 0%, #1570b5 100%);
  transition: width 0.3s ease;
  border-radius: 2px;
}
.unifideck-progress-indeterminate {
  position: absolute;
  inset: 0;
  width: 40%;
  background: linear-gradient(90deg, transparent 0%, #1a9fff 50%, transparent 100%);
  animation: unifideck-slide 1.5s linear infinite;
}
/* Decorative indeterminate shimmer: the sweep direction is purely
   cosmetic and need not flip for RTL. */
@keyframes unifideck-slide {
  0%   { transform: translateX(-100%); } /* rtl-ignore */
  100% { transform: translateX(250%); } /* rtl-ignore */
}

.unifideck-status-label {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  font-weight: 500;
}
.unifideck-status-detail {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.55);
  letter-spacing: 0.02em;
}

/* QuickAccess "Recently finished" → green Play badge. Solid green
 * base; brighten + ring on hover / focus / gamepad-focus so it
 * reads as the active "go" action next to the status badge. */
.unifideck-download-play-btn {
  background: #22c55e !important;
  transition: filter 0.15s ease, box-shadow 0.15s ease !important;
}
.unifideck-download-play-btn:hover,
.unifideck-download-play-btn:focus,
.unifideck-download-play-btn:focus-within,
.unifideck-download-play-btn.gpfocus {
  filter: brightness(1.15) !important;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.8) !important;
}

/* Spinning cloud icon while a save sync is in flight. */
@keyframes unifideck-cloud-spin {
  100% { transform: rotate(360deg); }
}
.unifideck-cloud-spin {
  animation: unifideck-cloud-spin 1s linear infinite;
}

/* Breathing animation for save discrepancies. */
@keyframes unifideck-breathe {
  0%, 100% {
    --unifideck-btn-bg: rgba(34, 197, 94, 0.08);
    --unifideck-btn-shadow: 0 0 0 1.5px rgba(34, 197, 94, 0.35) inset, 0 0 0px rgba(34, 197, 94, 0);
  }
  50% {
    --unifideck-btn-bg: rgba(34, 197, 94, 0.45);
    --unifideck-btn-shadow: 0 0 0 2.5px rgba(34, 197, 94, 0.95) inset, 0 0 12px rgba(34, 197, 94, 0.75);
  }
}
.unifideck-breathe:not(:hover):not(:focus):not(:focus-within):not(.gpfocus) {
  background: var(--unifideck-btn-bg, rgba(34, 197, 94, 0.18)) !important;
  box-shadow: var(--unifideck-btn-shadow, 0 0 0 2px rgba(34, 197, 94, 0.5) inset) !important;
  animation: unifideck-breathe 1.5s ease-in-out infinite !important;
}
`;

/** Inject the focus CSS into <head> exactly once.
 *
 *  Prefer rendering {@link PLAY_FOCUS_CSS} inline via `<style>` in
 *  the component subtree (see PlayShell / DownloadsTab) — that's
 *  what reliably lands the rules in the right CEF document. This
 *  head-injection is kept as a harmless fallback. */
export function injectPlayFocusStyles(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID)) return;

  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = PLAY_FOCUS_CSS;
  document.head.appendChild(style);
}
