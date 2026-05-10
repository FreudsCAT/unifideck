/**
 * LocaleContext — UI language preference.
 *
 * Reads the saved preference at mount, exposes the current
 * language tag, and wraps the i18next `changeLanguage` call
 * so consumers don't reach into the i18n module directly.
 *
 * Persistence is delegated to the backend (set_language_preference
 * RPC). The frontend's i18next instance is updated locally
 * on success, so the UI re-renders immediately while the
 * RPC completes in the background.
 */
import React, {
  createContext,
  FC,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import i18n from "i18next";
import { useRPCMutation, useRPCQuery } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";

/** Locale context value. */
interface LocaleContextValue {
  locale: string;
  loading: boolean;
  setLocale: (tag: string) => Promise<void>;
}

const Ctx = createContext<LocaleContextValue | null>(null);
/**
 * Provider that owns the active UI language. Reads
 * the persisted choice on mount, then propagates
 * changes to i18next and to the backend (so server
 * toasts come back already translated).
 */
export const LocaleProvider: FC<{ children: ReactNode }> = ({ children }) => {
  const [locale, setLocaleState] = useState<string>(i18n.language);
  const pref = useRPCQuery<[], { success: boolean; language: string }>(rpcRoutes.getLanguagePreference, []);

  const setMut = useRPCMutation<[string], { success: boolean }>(
    rpcRoutes.setLanguagePreference,
  );

  useEffect(() => {
    if (pref.data?.success && pref.data.language) {
      const tag = pref.data.language;
      if (tag && tag !== "auto" && tag !== locale) {
        void i18n.changeLanguage(tag);
        setLocaleState(tag);
      }
    }
  }, [pref.data]);

  /** Set locale. */
  const setLocale = useCallback(async (tag: string) => {
    await i18n.changeLanguage(tag);
    setLocaleState(tag);
    await setMut.mutate(tag); // Persist
  }, [setMut]);

  const value: LocaleContextValue = {
    locale,
    loading: pref.loading,
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
