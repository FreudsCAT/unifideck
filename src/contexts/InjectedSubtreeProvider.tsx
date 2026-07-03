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
 *   contexts those components need. Since the boot-time singletons
 *   now hold all reactive state, the contexts here are thin wrappers
 *   that subscribe to the same singletons — state is shared across
 *   both provider trees automatically.
 *
 * Note: `<ToastEventListener>` was global and now runs at boot
 * via `boot-event-listener.tsx`, so it's not needed here.
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
