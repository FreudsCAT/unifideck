/**
 * LocaleContext — UI language preference.
 *
 * The language is set at boot by `applyLanguagePreference()` in
 * `bootstrap-tasks.tsx`, so this provider reads directly from
 * `i18n.language` — no mount-time RPC fetch needed.
 *
 * Persistence of user-initiated changes is delegated to the
 * backend via `set_language_preference` RPC. The frontend's
 * i18next instance is updated locally on success, so the UI
 * re-renders immediately while the RPC completes in the
 * background.
 */
import {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useState,
} from "react";
import i18n from "i18next";
import { useRPCMutation } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";

/** Locale context value. */
interface LocaleContextValue {
  locale: string;
  loading: boolean;
  setLocale: (tag: string) => Promise<void>;
}

const Ctx = createContext<LocaleContextValue | null>(null);
/**
 * Provider that owns the active UI language. Reads from
 * `i18n.language` (set at boot by `applyLanguagePreference`),
 * then propagates user-initiated changes to i18next and to
 * the backend.
 */
export const LocaleProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [locale, setLocaleState] = useState<string>(i18n.language);

  const setMut = useRPCMutation<[string], { success: boolean }>(
    rpcRoutes.setLanguagePreference,
  );

  /** Set locale. */
  const setLocale = useCallback(
    async (tag: string) => {
      await i18n.changeLanguage(tag);
      setLocaleState(tag);
      await setMut.mutate(tag); // Persist
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
