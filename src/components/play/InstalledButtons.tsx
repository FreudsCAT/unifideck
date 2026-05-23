/**
 * InstalledButtons — Play section for installed Unifideck
 * games.
 *
 * Two buttons : "Play" (launches via SteamBridge) and
 * "Uninstall" (opens the confirm modal). The Play button
 * delegates to `useGameActions.launch` which routes through
 * SteamBridge.runGame ; the launch options are read from
 * the game info (cached). Uninstall opens
 * `<UninstallConfirmModal>` rather than acting immediately.
 */
import { FC, useCallback } from "react";
import { DialogButton, Focusable, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameActions } from "../../hooks/useGameActions";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import { UninstallConfirmModal } from "../modals/UninstallConfirmModal";

/** Props. */
interface Props {
  appId: number;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();

/**
 * Variant of the Play section shown when the game is
 * installed : Play (with optional Proton selector),
 * Update if available, Uninstall, and the cloud-save
 * sync indicator.
 */
export const InstalledButtons: FC<Props> = ({appId, bridge = defaultBridge}) => {
  const { t } = useTranslation();
  const { data: game, loading } = useGameInfo(appId);
  const actions = useGameActions(bridge);
  const toast = useToast();

  /** On play. */
  const onPlay = useCallback(() => {
    if (!game) return;
    const launchOpts = `${game.store}:${game.id}`;
    actions.launch(appId, launchOpts);
  }, [actions, appId, game]);

  /** On uninstall. */
  const onUninstall = useCallback(() => {
    if (!game) return;
    showModal(
      <UninstallConfirmModal
        gameId={appId}
        gameTitle={game.title}
        onConfirm={async () => {
          const r = await actions.uninstall(appId);
          if (r?.success) {
            toast.success(t("toasts.uninstallDone"));
          }
        }}
        closeModal={() => {}}
      />,
    );
  }, [actions, appId, game, t, toast]);
  return (
    <Focusable
      flow-children="row"
      onActivate={() => {}}
      style={{ display: "flex", gap: 8 }}
    >
      <DialogButton
        className="unifideck-play-btn"
        disabled={loading}
        onClick={onPlay}
      >
        {t("play.play")}
      </DialogButton>
      <DialogButton
        className="unifideck-stop-btn"
        disabled={loading || actions.isWorking}
        onClick={onUninstall}
      >
        {t("play.uninstall")}
      </DialogButton>
    </Focusable>
  );
};
