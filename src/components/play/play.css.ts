/**
 * Play-section focus styling — injected once into <head>.
 *
 * The Steam Deck gamepad cursor toggles a `.gpfocus` class on
 * whatever element is currently focused. Components in
 * `components/play/` apply `unifideck-<variant>-btn` class
 * names to their `DialogButton`s ; the rules below give each
 * variant a brand-coloured halo on focus (or pointer hover).
 *
 * Keeping the CSS in this module — rather than inline in each
 * component — preserves the PDF rule that play/* files are
 * pure presentation. The styles are scoped via class names so
 * no other plugin's buttons are affected.
 */

const STYLE_ID = "unifideck-play-focus-styles";

const CSS = `
.unifideck-install-btn,
.unifideck-play-btn,
.unifideck-resume-btn,
.unifideck-stop-btn,
.unifideck-cancel-btn {
  background: rgba(255, 255, 255, 0.1) !important;
  transition: background 0.15s ease, filter 0.15s ease !important;
}
.unifideck-install-btn:hover,
.unifideck-install-btn.gpfocus {
  background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
}
.unifideck-cancel-btn:hover,
.unifideck-cancel-btn.gpfocus {
  background: linear-gradient(135deg, #dc3545 0%, #c82333 100%) !important;
}
.unifideck-play-btn:hover,
.unifideck-play-btn.gpfocus {
  background: linear-gradient(135deg, #59bf40 0%, #459e31 100%) !important;
}
.unifideck-resume-btn:hover,
.unifideck-resume-btn.gpfocus {
  background: linear-gradient(135deg, #1a9fff 0%, #1570b5 100%) !important;
}
.unifideck-stop-btn:hover,
.unifideck-stop-btn.gpfocus {
  background: rgba(255, 255, 255, 0.2) !important;
}
.unifideck-install-btn:active,
.unifideck-cancel-btn:active,
.unifideck-play-btn:active,
.unifideck-resume-btn:active,
.unifideck-stop-btn:active {
  filter: brightness(0.85) !important;
}
`;

/** Inject the focus CSS into <head> exactly once. Idempotent
 *  — safe to call on every `PlaySectionWrapper` mount. */
export function injectPlayFocusStyles(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(STYLE_ID)) return;

  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = CSS;
  document.head.appendChild(style);
}
