/**
 * CleanupSection — wipe every Unifideck shortcut, artwork, and (opt.)
 * installed game directory. Two-step confirm pattern so a single
 * gamepad-button press can't trigger the destructive action.
 *
 * On success, also wipes the `[Unifideck] *` Steam Collections via
 * `deleteAllUnifideckCollections` and calls
 * `SteamClient.Apps.RemoveShortcut` for each deleted app_id so
 * Steam's in-memory state catches up.
 */
import { FC, useState } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  Field,
  ToggleField,
} from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useToast } from "../../hooks/useToast";
import { deleteAllUnifideckCollections } from "../../lib/steam-bridge/collection-manager";

interface CleanupResult {
  success: boolean;
  deleted_games: number;
  deleted_files_count: number;
  deleted_app_ids?: number[];
  error?: string | null;
}

export const CleanupSection: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const [showConfirm, setShowConfirm] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState(false);
  const { mutate, loading } = useRPCMutation<[boolean], CleanupResult>(
    rpcRoutes.performFullCleanup,
  );

  const handleDeleteAll = async () => {
    if (!showConfirm) {
      setShowConfirm(true);
      return;
    }
    const result = await mutate(deleteFiles);
    setShowConfirm(false);
    setDeleteFiles(false);
    if (!result) {
      toast.error(t("toasts.deleteFailed"), t("errors.unknown"));
      return;
    }
    if (result.success) {
      if (result.deleted_app_ids?.length) {
        const apps = (window as unknown as {
          SteamClient?: { Apps?: { RemoveShortcut: (id: number) => void } };
        }).SteamClient?.Apps;
        for (const id of result.deleted_app_ids) {
          try { apps?.RemoveShortcut(id); } catch { /* best effort */ }
        }
      }
      await deleteAllUnifideckCollections().catch((e) =>
        console.error("[Cleanup] delete collections failed", e));
      toast.success(
        t("toasts.cleanupSuccessful"),
        t("toasts.cleanupSuccessfulMessage", {
          games: result.deleted_games,
          artwork: 0,
          files: result.deleted_files_count,
        }),
      );
    } else {
      toast.error(t("toasts.deleteFailed"), result.error ?? t("errors.unknown"));
    }
  };

  return (
    <PanelSection title={t("cleanup.title")}>
      {!showConfirm ? (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={handleDeleteAll}
            disabled={loading}
          >
            {t("cleanup.deleteAll")}
          </ButtonItem>
        </PanelSectionRow>
      ) : (
        <>
          <PanelSectionRow>
            <Field
              label={t("cleanup.warningTitle")}
              description={t("cleanup.warningDescription")}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label={t("cleanup.deleteFilesLabel")}
              checked={deleteFiles}
              onChange={setDeleteFiles}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleDeleteAll}
              disabled={loading}
            >
              {loading ? t("cleanup.deleting") : t("cleanup.confirmDelete")}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => { setShowConfirm(false); setDeleteFiles(false); }}
              disabled={loading}
            >
              {t("cleanup.cancel")}
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
};
