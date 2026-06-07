/**
 * InjectedSubtreeProvider — minimal context wrapper for React
 * subtrees we splice into Steam's component tree (App Details,
 * library cards, etc).
 *
 * Why a second provider :
 *
 *   The plugin entry mounts `<RootProvider>` around the
 *   `<QuickAccessPanel>` — fine for everything rendered inside
 *   the QAM. But the App-Details patch (`views/AppDetailsPatch.tsx`)
 *   splices `<PlaySectionWrapper>` + `<GameInfoPanel>` directly
 *   into Steam's React tree. That tree is rendered by Steam, NOT
 *   under our `RootProvider`, so the injected components have no
 *   access to any context — `useDownloads`, `useAuth`, etc. all
 *   throw the "called outside <Provider>" guard.
 *
 *   This provider wraps the injected subtree with just the
 *   contexts those components need (Locale → Store → Auth →
 *   Sync → Download) — same composition as `RootProvider` but
 *   without `<ToastEventListener>`, which is global and must
 *   only be mounted once (by `RootProvider` in the QAM).
 *
 * The two provider trees keep independent React state, but the
 * underlying `EventBusClient` singleton + module-level caches
 * (`useGameInfo`, `useViewMode`, `gameStateVersion`) are shared,
 * so the two trees stay coherent.
 */
import { FC, ReactNode } from "react";
import { LocaleProvider } from "./LocaleContext";
import { StoreProvider } from "./StoreContext";
import { AuthProvider } from "./AuthContext";
import { SyncProvider } from "./SyncContext";
import { DownloadProvider } from "./DownloadContext";

/**
 * Minimal context stack for components mounted into Steam's
 * React tree by `AppDetailsPatch` (and any future router
 * patch).
 */
export const InjectedSubtreeProvider: FC<{ children: ReactNode }> = ({
  children,
}) => {
  return (
    <LocaleProvider>
      <StoreProvider>
        <AuthProvider>
          <SyncProvider>
            <DownloadProvider>{children}</DownloadProvider>
          </SyncProvider>
        </AuthProvider>
      </StoreProvider>
    </LocaleProvider>
  );
};
