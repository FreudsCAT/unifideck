/**
 * SteamRestartModal — prompts the user to restart Steam.
 *
 * Shown after a flow that writes to shortcuts.vdf : Steam
 * caches the file at startup, so changes only become visible
 * post-restart. The modal explains why and offers a
 * SteamClient.User.StartShutdown call (Steam's own restart
 * path). Restart-on-demand: never restart automatically.
 */
import { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Props. */
interface Props {
  closeModal?: () => void;
  store?: string;
  reason?: "sync" | "cleanup";
}

/**
 * Modal shown after operations that require Steam to
 * restart for changes to apply (shortcut creation, VDF
 * mutation). Offers a Restart-now button that calls
 * `SteamClient.User.StartRestart`.
 *
 * Title + description are derived from `reason`/`store` so a
 * single component covers sync, cleanup, and per-store flows.
 */
export const SteamRestartModal: FC<Props> = ({ closeModal, store, reason }) => {
  const { t } = useTranslation();
  const title =
    reason === "cleanup"
      ? t("confirmModals.steamRestartCleanupTitle")
      : t("restart.title");
  const description =
    reason === "cleanup"
      ? t("confirmModals.steamRestartCleanupDescription")
      : store
      ? t("confirmModals.steamRestartDescriptionStore", { store })
      : t("restart.body");
  return (
    <ConfirmModal
      strTitle={title}
      strDescription={description}
      strOKButtonText={t("restart.confirm")}
      strCancelButtonText={t("restart.later")}
      onOK={() => {
        // SteamClient.User.StartShutdown is observed-not-typed
        const sc = (
          window as {
            SteamClient?: {
              User?: { StartShutdown?: (force: boolean) => void };
            };
          }
        ).SteamClient;
        sc?.User?.StartShutdown?.(false);
        closeModal?.();
      }}
      onCancel={() => closeModal?.()}
    />
  );
};
