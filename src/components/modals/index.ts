/**
 * Modals — barrel export.
 *
 * Five pieces : AccountSwitchModal (Steam account change
 * prompt), SteamRestartModal (post-shortcut-write reboot
 * prompt), UninstallConfirmModal (delete confirmation),
 * CloudSaveConflictModal (cloud save resolution), and
 * ToastEventListener (event-driven host that displays toasts
 * and opens modals based on backend events).
 *
 * Modals receive `closeModal` from `showModal()` and return
 * JSX wrapped in `<ConfirmModal>` from `@decky/ui`. The
 * listener returns null and is mounted by RootProvider.
 */
export { AccountSwitchModal } from "./AccountSwitchModal";
export { SteamRestartModal } from "./SteamRestartModal";
export { UninstallConfirmModal } from "./UninstallConfirmModal";
export { CloudSaveConflictModal } from "./CloudSaveConflictModal";
export { ToastEventListener } from "./ToastEventListener";
