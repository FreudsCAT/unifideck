/**
 * gog-language-match — normalize the wildly inconsistent language
 * labels gogdl emits and match them against the user's locale.
 *
 * GOG games describe their installable languages in no fixed
 * format. Across titles we see the full English name ("English",
 * "Spanish"), native names ("Deutsch", "Français"), 2-letter ISO
 * 639-1 ("en", "es"), 3-letter ISO 639-2 ("eng", "spa") plus GOG
 * legacy quirks ("esp" for Spanish, "br" for Brazilian Portuguese,
 * "cn" for Chinese), BCP-47 tags ("en-US", "pt-BR"), and composite
 * "Name (code)" forms ("English (en)", "Spanish (esp)").
 *
 * `normalizeGogLanguage` collapses any of those to an ISO 639-1
 * base code; `matchGogLanguage` uses it to find which raw option
 * a game offers corresponds to a target locale tag. Both degrade
 * gracefully — an unrecognised label yields `null` rather than a
 * wrong guess, so the install modal simply falls back.
 *
 * Note: matching is at the base-language level (e.g. zh-CN and
 * zh-TW both normalize to "zh"); `matchGogLanguage` tries an exact
 * tag match first so a well-formed "zh-TW" still wins over a bare
 * "Chinese" when both are offered.
 */

/** Each group is `[isoBase, ...aliases]`. Aliases are matched
 *  case-insensitively. The base maps to itself. */
const ALIAS_GROUPS: Array<[string, ...string[]]> = [
  ["en", "eng", "english"],
  ["fr", "fra", "fre", "french", "français", "francais"],
  ["de", "deu", "ger", "german", "deutsch"],
  ["es", "esp", "spa", "spanish", "español", "espanol", "castellano"],
  ["it", "ita", "italian", "italiano"],
  [
    "pt",
    "por",
    "portuguese",
    "português",
    "portugues",
    "brazilian",
    "br", // GOG legacy code for Brazilian Portuguese
  ],
  ["ru", "rus", "russian", "русский"],
  ["pl", "pol", "polish", "polski"],
  [
    "zh",
    "zho",
    "chi",
    "chinese",
    "cn", // GOG legacy code
    "中文",
    "简体中文",
    "繁體中文",
    "繁体中文",
    "simplified chinese",
    "traditional chinese",
  ],
  ["ja", "jpn", "jp", "japanese", "日本語"],
  ["ko", "kor", "korean", "한국어"],
  ["nl", "nld", "dut", "dutch", "nederlands"],
  ["tr", "tur", "turkish", "türkçe", "turkce"],
  ["uk", "ukr", "ukrainian", "українська"],
  ["cs", "ces", "cze", "czech", "čeština", "cestina"],
  ["hu", "hun", "hungarian", "magyar"],
  ["sv", "swe", "swedish", "svenska"],
  ["da", "dan", "danish", "dansk"],
  ["fi", "fin", "finnish", "suomi"],
  [
    "no",
    "nor",
    "norwegian",
    "norsk",
    "nb",
    "nob",
    "bokmål",
    "bokmal",
    "nn",
    "nno",
    "nynorsk",
  ],
  ["ar", "ara", "arabic", "العربية"],
  ["th", "tha", "thai", "ไทย"],
  ["el", "gre", "ell", "greek", "ελληνικά"],
  ["ro", "ron", "rum", "romanian", "română", "romana"],
  ["bg", "bul", "bulgarian", "български"],
];

const ALIASES: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const [base, ...aliases] of ALIAS_GROUPS) {
    map[base] = base;
    for (const alias of aliases) map[alias] = base;
  }
  return map;
})();

/** Look up a single cleaned token: try the whole token, then its
 *  base segment before a region/script suffix ("en-us" → "en"). */
function lookupToken(token: string): string | null {
  const t = token.trim().replace(/_/g, "-");
  if (!t) return null;
  if (ALIASES[t]) return ALIASES[t];
  const base = t.split("-")[0];
  if (base && ALIASES[base]) return ALIASES[base];
  return null;
}

/** Split a raw label into candidate tokens, unpacking a trailing
 *  "Name (code)" parenthetical into both halves. */
function tokensFrom(raw: string): string[] {
  const s = raw.trim().toLowerCase();
  const tokens: string[] = [];
  const paren = s.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
  if (paren) {
    if (paren[1]) tokens.push(paren[1].trim());
    if (paren[2]) tokens.push(paren[2].trim());
  }
  // Whole string with parens flattened, then verbatim.
  tokens.push(s.replace(/[()]/g, " ").trim());
  tokens.push(s);
  return tokens;
}

/**
 * Normalize any GOG language label to an ISO 639-1 base code, or
 * `null` if unrecognised.
 */
export function normalizeGogLanguage(raw: string): string | null {
  if (!raw) return null;
  for (const token of tokensFrom(raw)) {
    const hit = lookupToken(token);
    if (hit) return hit;
  }
  return null;
}

/**
 * Find which raw option corresponds to `targetTag`. Prefers an
 * exact (case-insensitive, `_`/`-`-normalized) tag match so a
 * well-formed regional tag wins over a generic base match;
 * otherwise falls back to base-language normalization. Returns the
 * matching raw option (verbatim, suitable for passing to gogdl) or
 * `null` when the game offers no equivalent.
 */
export function matchGogLanguage(
  rawOptions: string[],
  targetTag: string,
): string | null {
  if (!targetTag || rawOptions.length === 0) return null;
  // 1. Exact tag match.
  const targetLc = targetTag.trim().toLowerCase().replace(/_/g, "-");
  for (const opt of rawOptions) {
    if (opt.trim().toLowerCase().replace(/_/g, "-") === targetLc) return opt;
  }
  // 2. Base-language match.
  const target = normalizeGogLanguage(targetTag);
  if (!target) return null;
  for (const opt of rawOptions) {
    if (normalizeGogLanguage(opt) === target) return opt;
  }
  return null;
}
