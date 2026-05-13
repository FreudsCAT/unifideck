/**
 * i18n subpackage — barrel export.
 *
 * Single import surface for translation runtime API. The
 * runtime contract (initI18n, changeLanguage, isRTL,
 * getSupportedLanguages) is defined in `./translations`.
 * Components import from this barrel instead of reaching
 * into translations.ts directly so a future refactor of the
 * loader (e.g. swapping i18next for a different backend) is
 * a one-file change.
 */
export * from "./translations";
