/**
 * NotInstalledButtons — Play section for not-yet-installed
 * Unifideck games.
 *
 * Renders an "Install" button that triggers `useGameActions
 * .install()` with the canonical store + game_id pulled
 * from `useGameInfo`. While the install RPC is in flight we
 * disable the button and show a spinner.
 *
 * The "Storage location" picker is NOT inlined here — it is
 * a separate modal opened on click when the user has more
 * than one storage location. The modal itself lives in
 * `components/modals/StoragePickerModal.tsx`.
 */
import React, { FC, useCallback } from "react";
import { DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameActions } from "../../hooks/useGameActions";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";

/** Props. */
interface Props {
  appId: number;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();

/**
 * Variant of the Play section shown when the game is not
 * installed yet : Install button, store selector if the
 * game is owned in multiple stores, and Cancel/Hide.
 */
export const NotInstalledButtons: FC<Props> = ({appId, bridge = defaultBridge}) => {
  const { t } = useTranslation();
  const { data: game, loading } = useGameInfo(appId);
  const actions = useGameActions(bridge);
  const toast = useToast();

  /** On install. */
  const onInstall = useCallback(async () => {
    if (!game) return;
    const result = await actions.install(game.store, game.id);
    if (result?.success) {
      toast.success(
        t("toasts.downloadQueued"),
        t("toasts.downloadQueuedBody", { title: game.title }),
      );
    } else {
      toast.error(
        t("toasts.downloadFailed"),
        result?.error ?? t("toasts.downloadFailedUnknown"),
      );
    }
  }, [actions, game, t, toast]);
  return (
    <div style={{ display: "flex", gap: 8 }}>
      <DialogButton
        disabled={loading || actions.isWorking || !game}
        onClick={onInstall}
      >
        {actions.isWorking ? t("play.installing") : t("play.install")}
      </DialogButton>
    </div>
  );
};
