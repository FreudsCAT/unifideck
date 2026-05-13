/**
 * AccountSwitchModal — post-Steam-account-change prompt.
 *
 * Shown at plugin startup when the backend detects that the
 * current Steam user differs from the user that owns the
 * existing Unifideck registry/auth tokens. Offers three
 * actions :
 *  - Migrate : copy shortcuts + artwork to the new user
 *              (calls `migrate_account_data` then prompts
 *              for a Steam restart so the new shortcuts.vdf
 *              becomes visible).
 *  - Clear   : wipe stored auth tokens (`clear_store_auths`)
 *              — safer for shared devices.
 *  - Skip    : keep the legacy user's data as-is.
 *
 * Driven by the bootstrap-tasks `checkAccountSwitch` flow
 * which calls `check_account_switch` once at plugin load.
 */
import React, { FC } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";

/** Props. */
interface Props {
  hasRegistry: boolean;
  hasAuthTokens: boolean;
  onMigrate: () => Promise<void> | void;
  onClearAuths: () => Promise<void> | void;
  onSkip: () => void;
  closeModal: () => void;
}

/**
 * Modal shown when bootstrap detects that the active
 * Steam user has changed since the last run. Offers
 * Continue (clear caches) or Stay-logged-out paths.
 */
export const AccountSwitchModal: FC<Props> = ({
  hasAuthTokens, onMigrate, onClearAuths, onSkip, closeModal,
}) => {
  const { t } = useTranslation();
  return (
    <ConfirmModal
      strTitle={t("accountSwitch.title")}
      strDescription={
        hasAuthTokens
          ? t("accountSwitch.bodyWithTokens")
          : t("accountSwitch.bodyNoTokens")
      }
      strOKButtonText={t("accountSwitch.migrate")}
      strCancelButtonText={t("accountSwitch.skip")}
      strMiddleButtonText={
        hasAuthTokens ? t("accountSwitch.clearAuths") : undefined
      }
      bAlertDialog={false}
      onOK={async () => {
        await onMigrate();
        closeModal();
      }}
      onMiddleButton={
        hasAuthTokens
          ? async () => {
              await onClearAuths();
              closeModal();
            }
          : undefined
      }
      onCancel={() => {
        onSkip();
        closeModal();
      }}
    />
  );
};
