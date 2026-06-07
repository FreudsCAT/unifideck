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

const STYLE_ID = "unifideck-play-focus-styles";

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
.unifideck-stop-btn.gpfocus,
.unifideck-icon-btn:hover,
.unifideck-icon-btn.gpfocus {
  background: rgba(255, 255, 255, 0.22) !important;
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
  box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.7) !important;
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
