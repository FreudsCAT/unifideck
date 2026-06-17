/**
 * AccountSwitchModal — post-Steam-account-change prompt.
 *
 * Shown at plugin startup when the backend detects that the
 * current Steam user differs from the user that owns the
 * existing Unifideck registry/auth tokens. Offers three
 * actions : Migrate, Fresh-Start (clear auths), Skip.
 *
 * Driven by the bootstrap-tasks `checkAccountSwitch` flow.
 */
import { FC } from "react";
import { ConfirmModal, DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaExchangeAlt, FaForward, FaTrashAlt } from "react-icons/fa";

interface Props {
  hasRegistry: boolean;
  hasAuthTokens: boolean;
  onMigrate: () => Promise<void> | void;
  onClearAuths: () => Promise<void> | void;
  onSkip: () => void;
  closeModal: () => void;
}

export const AccountSwitchModal: FC<Props> = ({
  hasRegistry,
  hasAuthTokens,
  onMigrate,
  onClearAuths,
  onSkip,
  closeModal,
}) => {
  const { t } = useTranslation();
  return (
    <>
      <style>{`
        .unifideck-account-switch + div { display: none !important; }
        .DialogFooter { display: none !important; }
      `}</style>
      <ConfirmModal
        strTitle={t("accountSwitch.title")}
        strDescription=""
        bHideCloseIcon={false}
        onOK={closeModal}
        onCancel={closeModal}
      >
        <div className="unifideck-account-switch" style={{ padding: "10px 0" }}>
          <div
            style={{
              marginBottom: 20,
              color: "#ccc",
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            {t("accountSwitch.description")}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {hasRegistry && (
              <DialogButton
                onClick={async () => {
                  closeModal();
                  await onMigrate();
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                }}
              >
                <FaExchangeAlt /> {t("accountSwitch.migrate")}
              </DialogButton>
            )}
            {hasAuthTokens && (
              <DialogButton
                onClick={async () => {
                  closeModal();
                  await onClearAuths();
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 8,
                  color: "#ef4444",
                }}
              >
                <FaTrashAlt /> {t("accountSwitch.freshStart")}
              </DialogButton>
            )}
            <DialogButton
              onClick={() => {
                closeModal();
                onSkip();
              }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
                opacity: 0.7,
              }}
            >
              <FaForward /> {t("accountSwitch.skip")}
            </DialogButton>
          </div>
        </div>
      </ConfirmModal>
    </>
  );
};
