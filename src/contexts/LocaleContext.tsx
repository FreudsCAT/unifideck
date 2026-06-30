/**
 * LocaleContext — UI language preference.
 *
 * The value owned here is the user's *preference* — the "auto"
 * sentinel or a concrete supported tag — NOT the resolved active
 * locale. The dropdown binds to it, so "Auto-detect" stays shown
 * as selected while i18next runs in the detected language.
 *
 * Source of truth & sync:
 *  - Initial value is read synchronously from
 *    `localStorage[LANG_STORAGE_KEY]`, defaulting to "auto".
 *  - On mount we also fetch `get_language_preference` and
 *    reconcile, since neither `initI18n()` nor
 *    `applyLanguagePreference()` is awaited before this provider
 *    mounts, and the backend value survives across devices.
 *  - User changes resolve i18next ("auto" → detected tag),
 *    mirror to localStorage, set local state, and persist to the
 *    backend via `set_language_preference`.
 */
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import i18n from "i18next";
import { useRPCMutation, unwrapRpcEnvelope } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import {
  AUTO_DETECT,
  LANG_STORAGE_KEY,
  resolveAutoLanguage,
} from "../i18n/translations";
import { call } from "@decky/api";

/** Locale context value. */
interface LocaleContextValue {
  locale: string;
  loading: boolean;
  setLocale: (tag: string) => Promise<void>;
}

/** Shape returned by `get_language_preference` (the `data`
 *  payload, after the `{success, error, data}` envelope is
 *  unwrapped). */
interface LanguagePref {
  locale: string;
}

const Ctx = createContext<LocaleContextValue | null>(null);

/** Read the persisted preference synchronously for the first
 *  render. Defaults to "auto" so a fresh install shows
 *  "Auto-detect" selected. */
function readStoredPreference(): string {
  try {
    return localStorage.getItem(LANG_STORAGE_KEY) || AUTO_DETECT;
  } catch {
    return AUTO_DETECT;
  }
}

/** Apply a preference to i18next, resolving "auto" to the
 *  detected tag (i18next has no "auto" bundle). */
async function applyPreferenceToI18n(pref: string): Promise<void> {
  const tag = pref === AUTO_DETECT ? resolveAutoLanguage() : pref;
  if (i18n.language !== tag) {
    await i18n.changeLanguage(tag);
  }
}

/**
 * Provider that owns the active UI language *preference*. Reads
 * it from localStorage, reconciles with the backend on mount,
 * then propagates user-initiated changes to i18next, localStorage
 * and the backend.
 */
export const LocaleProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [locale, setLocaleState] = useState<string>(readStoredPreference);
  // Set once the user picks a language, so the async mount fetch
  // below never clobbers an in-flight selection with a stale
  // backend value.
  const userTouchedRef = useRef(false);

  const setMut = useRPCMutation<[string], { success: boolean }>(
    rpcRoutes.setLanguagePreference,
  );

  // Reconcile with the durable backend preference once, on mount.
  // The bootstrap path that sets i18next isn't awaited, so the
  // backend is the authority for what the user last chose.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        // get_language_preference returns the standard
        // {success, error, data} envelope — unwrap to the {locale}
        // payload (a raw `call` does NOT unwrap, unlike useRPC).
        const raw = await call<[], unknown>(rpcRoutes.getLanguagePreference);
        const r = unwrapRpcEnvelope<LanguagePref>(raw, {
          route: rpcRoutes.getLanguagePreference,
          throwing: false,
        });
        const pref = r?.locale || AUTO_DETECT;
        if (cancelled || userTouchedRef.current) return;
        await applyPreferenceToI18n(pref);
        try {
          localStorage.setItem(LANG_STORAGE_KEY, pref);
        } catch {
          // ignore quota/availability errors
        }
        if (!cancelled && !userTouchedRef.current) setLocaleState(pref);
      } catch {
        // Backend not ready — keep the localStorage-derived value.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** Set locale preference ("auto" or a concrete tag). */
  const setLocale = useCallback(
    async (pref: string) => {
      userTouchedRef.current = true;
      await applyPreferenceToI18n(pref);
      try {
        localStorage.setItem(LANG_STORAGE_KEY, pref);
      } catch {
        // ignore quota/availability errors
      }
      setLocaleState(pref);
      await setMut.mutate(pref); // Persist
    },
    [setMut],
  );

  const value: LocaleContextValue = {
    locale,
    loading: false,
    setLocale,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

/**
 * Access the LocaleContext value. Throws if used
 * outside `<LocaleProvider>`.
 *
 * @throws Error when the provider is missing.
 */
export function useLocale(): LocaleContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLocale called outside <LocaleProvider>");
  return v;
}
