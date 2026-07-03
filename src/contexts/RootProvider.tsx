/**
 * RootProvider — composition of the five domain contexts.
 *
 * Order matters and is documented inside the JSX :
 *  Locale outermost   — i18n initialised before any UI string
 *  Store              — load store registry
 *  Auth               — depends on Store (per-store status)
 *  Sync               — emits events Auth may react to
 *  Download outermost — depends on Sync (post-sync downloads)
 *
 * The plugin entry mounts a single <RootProvider> around the
 * entire React subtree so every component has access to all
 * five contexts via the matching `useX` hook.
 *
 * <ToastEventListener> is mounted at the bottom of the tree
 * (alongside `children`) so it has access to all contexts
 * AND to the EventBusClient. It listens to LAUNCHER_STAGE,
 * STORE_ERROR, etc. and renders toasts/modals based on the
 * payloads — this is the canonical bidirectional bridge for
 * backend → user → backend round-trips.
 */
import { FC, ReactNode } from "react";
import { LocaleProvider } from "./LocaleContext";
import { StoreProvider } from "./StoreContext";
import { LibraryProvider } from "./LibraryContext";
import { AuthProvider } from "./AuthContext";
import { SyncProvider } from "./SyncContext";
import { DownloadProvider } from "./DownloadContext";

/**
 * Composition of all five context providers in the order
 * dictated by their dependency graph (Locale →
 * Store → Auth → Sync → Download). Mounted once at the
 * top of the React tree by the plugin entry point.
 */
export const RootProvider: FC<{ children: ReactNode }> = ({ children }) => {
  return (
    <LocaleProvider>
      <StoreProvider>
        <LibraryProvider>
          <AuthProvider>
            <SyncProvider>
              <DownloadProvider>{children}</DownloadProvider>
            </SyncProvider>
          </AuthProvider>
        </LibraryProvider>
      </StoreProvider>
    </LocaleProvider>
  );
};
