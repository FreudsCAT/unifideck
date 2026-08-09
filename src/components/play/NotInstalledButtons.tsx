/**
 * NotInstalledButtons — Play section variant for an
 * uninstalled Unifideck game.
 *
 * Single horizontal row inside {@link PlayShell} :
 *
 *   [ Install ]   Space Required: 564 MB           [ 🎮 ] [ ⚙ ]
 *
 * Install delegates to ``useInstallFlow.start(game)`` which
 * runs the storage picker + GOG language modal before
 * queueing the download.
 */
import { FC, useCallback } from "react";
import { DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaDownload } from "react-icons/fa";
import { SteamControllerIcon, SteamGearIcon } from "../shared";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useInstallFlow } from "../../hooks/useInstallFlow";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import { openNativeAppManageMenu } from "../../utils/nativeAppMenu";
import {
  PlayShell,
  MetaInline,
  IconGroup,
  actionBtnStyle,
  iconBtnStyle,
  actionBtnClass,
  iconBtnClass,
  controllerBtnClass,
} from "./PlayMeta";

interface Props {
  appId: number;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();

function openControllerConfig(appId: number): void {
  (
    window as unknown as {
      SteamClient?: {
        Apps?: { ShowControllerConfigurator?: (id: number) => void };
      };
    }
  ).SteamClient?.Apps?.ShowControllerConfigurator?.(appId);
}

function openAppSettings(appId: number): void {
  (
    window as unknown as {
      SteamClient?: {
        Apps?: { OpenAppSettingsDialog?: (id: number, page: string) => void };
      };
    }
  ).SteamClient?.Apps?.OpenAppSettingsDialog?.(appId, "general");
}

export const NotInstalledButtons: FC<Props> = ({
  appId,
  bridge = defaultBridge,
}) => {
  const { t } = useTranslation();
  const { data: game, loading } = useGameInfo(appId);
  const installFlow = useInstallFlow(bridge);
  const toast = useToast();

  const onInstall = useCallback(async () => {
    if (!game) return;
    const result = await installFlow.start(game);
    if (result == null) return;
    if (result.success) {
      toast.success(
        t("toasts.downloadQueued"),
        t("toasts.downloadQueuedBody", { title: game.title }),
      );
    } else {
      toast.error(
        t("toasts.downloadFailed"),
        result.error ?? t("toasts.downloadFailedUnknown"),
      );
    }
  }, [installFlow, game, t, toast]);

  return (
    // autoFocus is intentional: claims gamepad focus for the primary action
    // eslint-disable-next-line jsx-a11y/no-autofocus
    <PlayShell autoFocus>
      <DialogButton
        className={actionBtnClass("unifideck-install-btn")}
        disabled={loading || installFlow.isWorking || !game}
        onClick={onInstall}
        style={actionBtnStyle}
      >
        <FaDownload />
        {installFlow.isWorking
          ? t("playButton.installing")
          : t("playButton.install")}
      </DialogButton>

      <MetaInline
        sizeBytes={game?.size_bytes}
        showLastPlayed
        appId={appId}
        store={game?.store}
        gameId={game?.id}
      />

      <IconGroup>
        <DialogButton
          className={controllerBtnClass()}
          style={iconBtnStyle}
          onClick={() => openControllerConfig(appId)}
          aria-label={t("playButton.controllerConfig")}
        >
          <SteamControllerIcon />
        </DialogButton>
        <DialogButton
          className={iconBtnClass()}
          style={iconBtnStyle}
          onClick={(e) => {
            // Open Steam's native app menu (Manage / Properties / …),
            // matching the native gear; fall back to Properties directly.
            if (!openNativeAppManageMenu(e?.currentTarget as HTMLElement)) {
              openAppSettings(appId);
            }
          }}
          aria-label={t("playButton.appSettings")}
        >
          <SteamGearIcon />
        </DialogButton>
      </IconGroup>
    </PlayShell>
  );
};
