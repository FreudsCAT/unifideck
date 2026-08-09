/**
 * pick-default-language — which install language to pre-select.
 *
 * Split out of `LanguageSelectModal` so it can be unit-tested:
 * React isn't installed as a dev dependency (Decky supplies it at
 * runtime), so vitest cannot import a `.tsx` module.
 *
 * The order matters. `options[0]` is a genuine last resort and is only
 * safe while an English choice is actually present in the list. Epic's
 * Selective Downloads configs omit English — it's the untagged base
 * game, not a downloadable pack — and feeding that list straight in
 * made this pre-select Deutsch for English users, who then downloaded a
 * German voice pack by accepting the dialog. The backend therefore
 * always includes a base-language entry for such titles
 * (`py_modules/unifideck/stores/epic/sdl.py`).
 */
import { matchGogLanguage } from "./gog-language-match";

/**
 * Pick the option to pre-select: the user's language if the game
 * offers it, else an English option, else the first listed —
 * normalizing the inconsistent label formats stores emit. The user
 * still confirms (or changes) before anything installs.
 */
export function pickDefaultLanguage(
  options: string[],
  preferredTag: string,
): string {
  return (
    matchGogLanguage(options, preferredTag) ??
    matchGogLanguage(options, "en") ??
    options[0]
  );
}
