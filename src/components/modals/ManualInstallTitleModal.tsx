/**
 * ManualInstallTitleModal — confirm the game's title before adding it.
 *
 * The title is what unifiDB metadata and SteamGridDB artwork are looked
 * up by (a manual game has no store id to match on), and it becomes the
 * Steam shortcut's name — so the user gets to fix the guess derived
 * from the installer's file name before anything is created.
 */
import { FC, useState } from "react";
import { ConfirmModal, TextField } from "@decky/ui";
import { useTranslation } from "react-i18next";

interface Props {
  installerPath: string;
  suggestedTitle: string;
  onConfirm: (title: string) => void;
  closeModal?: () => void;
}

export const ManualInstallTitleModal: FC<Props> = ({
  installerPath,
  suggestedTitle,
  onConfirm,
  closeModal,
}) => {
  const { t } = useTranslation();
  const [title, setTitle] = useState(suggestedTitle);
  const clean = title.trim();

  const confirm = () => {
    if (!clean) return;
    onConfirm(clean);
    closeModal?.();
  };

  return (
    <ConfirmModal
      strTitle={t("manualInstall.titleModalTitle")}
      strOKButtonText={t("manualInstall.titleModalConfirm")}
      bOKDisabled={!clean}
      onOK={confirm}
      onCancel={() => closeModal?.()}
    >
      <div style={{ marginBottom: 8, opacity: 0.8, fontSize: "0.9em" }}>
        {t("manualInstall.titleModalSubtitle")}
      </div>
      <TextField
        value={title}
        onChange={(e) => setTitle(e.currentTarget.value)}
        focusOnMount
      />
      <div
        dir="ltr"
        style={{
          marginTop: 8,
          opacity: 0.5,
          fontSize: "0.8em",
          wordBreak: "break-all",
        }}
      >
        {installerPath}
      </div>
    </ConfirmModal>
  );
};
