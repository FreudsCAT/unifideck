/**
 * React contexts — barrel export.
 *
 * Five domain-specific contexts plus the composition helper
 * `RootProvider` that wires them in the right order. Order
 * matters because `AuthContext` depends on `StoreContext`
 * (auth status is read per registered store), and
 * `DownloadContext` listens to events that other contexts
 * may emit transitively.
 */
export { StoreProvider, useStores } from "./StoreContext";
export { SyncProvider, useSync } from "./SyncContext";
export { AuthProvider, useAuth } from "./AuthContext";
export { DownloadProvider, useDownloads } from "./DownloadContext";
export { LocaleProvider, useLocale } from "./LocaleContext";
export { RootProvider } from "./RootProvider";
export { InjectedSubtreeProvider } from "./InjectedSubtreeProvider";
