/**
 * StorageBrowserModal — full-screen picker for the custom
 * install path.
 *
 * Wraps `StoragePathPicker` in a ConfirmModal so the user gets
 * the full-screen browser experience the legacy StorageSettings
 * exposed (rather than the inline picker squeezed into the
 * Quick-Access panel). The modal owns its own confirm action
 * and forwards the picked path to the caller on dismiss.
 *
 * Pure presentational : storage RPCs flow through
 * `useStorageConfig` in the parent ; this modal is only the
 * UI shell.
 */
import { FC, useState } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { StoragePathPicker } from "../settings/StoragePathPicker";

interface Props {
  startPath: string;
  onConfirm: (path: string) => Promise<void> | void;
  closeModal?: () => void;
}

/**
 * Modal hosting a `StoragePathPicker`. Confirm = close +
 * invoke `onConfirm(path)` with the most recently selected
 * directory.
 */
export const StorageBrowserModal: FC<Props> = ({
  startPath, onConfirm, closeModal,
}) => {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<string>(startPath);

  return (
    <ConfirmModal
      strTitle={t("storageSettings.browseButton")}
      strOKButtonText={t("storageSettings.selectFolder")}
      strCancelButtonText={t("common.cancel")}
      onOK={async () => { closeModal?.(); await onConfirm(picked); }}
      onCancel={closeModal}
    >
      <StoragePathPicker
        startPath={startPath}
        onConfirm={(path) => { setPicked(path); }}
      />
    </ConfirmModal>
  );
};
