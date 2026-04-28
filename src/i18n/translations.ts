/**
 * src/i18n/translations.ts — i18n loader + runtime API.
 *
 * This module is intentionally thin. The canonical list of
 * supported languages, their native names, their RTL flag, and
 * the static imports of every {tag}.json locale file all live
 * in `locales.generated.ts`, which is auto-generated from
 * `defaults/config.json` by `scripts/gen_locale_imports.py`.
 *
 * Adding a new language requires ONLY adding an entry to the
 * `i18n.locales` array in defaults/config.json. The CI workflow
 * then regenerates `locales.generated.ts` and calls DeepL to
 * produce the corresponding `{tag}.json` file. This file (the
 * runtime API) never needs to change.
 *
 * What lives here:
 * - `initI18n()`: one-time i18next setup with all bundled
 * locales and RTL direction applied to the document root
 * - `changeLanguage()`: runtime language switch that persists
 * the choice and updates the document direction
 * - `isRTL()`: convenience re-export from the generated catalog
 * - `getSupportedLanguages()`: returns the canonical tuple
 * - `detectInitialLanguage()`: localStorage → navigator → en-US
 *
 * Reference: Technical Document v1.0 — Section 10 (i18n),
 * Figure 85.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
// Everything locale-related comes from the generated catalog.
// Editing the list of supported languages means editing
// defaults/config.json, not this file.
import {
 SUPPORTED_LANGUAGES,
 SupportedLanguage,
 LANGUAGE_NAMES,
 RTL_LANGUAGES,
 LOCALE_RESOURCES,
} from "./locales.generated";
// Re-export so existing callers can import these from
// "./translations" without caring that the data actually
// comes from a generated file
export {
 SUPPORTED_LANGUAGES,
 LANGUAGE_NAMES,
 RTL_LANGUAGES,
};
export type { SupportedLanguage };
/**
 * Return true if the given language tag should be rendered in
 * right-to-left mode. Centralized here so callers don't have to
 * know which languages are RTL.
 */
export function isRTL(lang: SupportedLanguage): boolean {
 return RTL_LANGUAGES.has(lang);
}

/**
 * Apply the correct `dir` and `lang` attributes to the document
 * root. Idempotent — safe to call multiple times with the same
 * value. Called internally by initI18n() and changeLanguage();
 * most callers should never invoke it directly.
 *
 * When an RTL language becomes active, the entire React tree
 * flips naturally: flexbox row directions reverse, text-align
 * defaults to right, scrollbars move to the left, etc. Any
 * component that wants to opt out of flipping for a specific
 * element can override with an explicit `dir="ltr"` attribute.
 */
function applyDirection(lang: SupportedLanguage): void {
 if (typeof document === "undefined") return; // SSR safety
 document.documentElement.dir = isRTL(lang) ? "rtl" : "ltr";
 document.documentElement.lang = lang;
}
/**
 * Initialize i18next with all bundled locales. Called once at
 * plugin startup. Safe to call multiple times (idempotent).
 *
 * Also applies the correct `dir` attribute to the document root
 * based on the detected initial language, so RTL languages
 * render correctly from the first frame.
 */
export async function initI18n(): Promise<void> {
 if (i18n.isInitialized) return;
 const initialLang = detectInitialLanguage();
 await i18n
 .use(initReactI18next)
 .init({
 resources: LOCALE_RESOURCES,
 lng: initialLang,
 fallbackLng: "en-US",
 interpolation: { escapeValue: false },
 returnEmptyString: false,
 });
 applyDirection(initialLang);
}
/**
 * Change the current UI language at runtime, persist the choice
 * to localStorage, and update the document direction so RTL
 * languages flip immediately without a page reload.
 */
export async function changeLanguage(
 lang: SupportedLanguage,
): Promise<void> {
 await i18n.changeLanguage(lang);
 applyDirection(lang);
 try {
 localStorage.setItem("unifideck.lang", lang);
 } catch {
 // ignore quota/availability errors
 }
}
/** Return the list of supported language tags. */
export function getSupportedLanguages(): readonly SupportedLanguage[] {
 return SUPPORTED_LANGUAGES;
}
/**
 * Detect the initial language from localStorage, then the
 * browser's navigator.language, then fall back to en-US.
 */

function detectInitialLanguage(): SupportedLanguage {
 try {
 const saved = localStorage.getItem("unifideck.lang");
 if (saved && (SUPPORTED_LANGUAGES as readonly string[]).includes(saved)) {
 return saved as SupportedLanguage;
 }
 } catch {
 // localStorage unavailable
 }
 // Fall back to browser language if it matches a supported tag
 const browserLang = navigator.language || "en-US";
 const match = SUPPORTED_LANGUAGES.find(
 (l) => l === browserLang || l.startsWith(browserLang.split("-")[0]),
 );
 return match || "en-US";
}
