/**
 * XCloudButtons — Play section variant for Xbox Cloud Gaming titles.
 *
 * Xbox Game Pass cloud games aren't installed locally; they stream in
 * an Edge kiosk. The Play button goes through the same Steam ``RunGame``
 * path as native games (→ ``unifideck-launcher`` → the ``exe="xcloud"``
 * games.map sentinel → ``_launch_xcloud``), so controller config and
 * Steam input work. If Steam's Apps surface is unavailable we fall back
 * to ``steam://openurl`` on the xCloud launch URL.
 */
import { FC, useCallback } from "react";
import { DialogButton } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaCloud, FaGamepad, FaCog } from "react-icons/fa";
import { launchAppWithConfiguredGamepad } from "../../utils/controllerConfig";
import {
  PlayShell,
  MetaInline,
  IconGroup,
  actionBtnStyle,
  iconBtnStyle,
} from "./PlayMeta";

interface Props {
  appId: number;
  /** Store game id (Microsoft productId) — used for the openurl fallback. */
  gameId: string;
}

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

export const XCloudButtons: FC<Props> = ({ appId, gameId }) => {
  const { t } = useTranslation();

  const onPlay = useCallback(async () => {
    const launched = await launchAppWithConfiguredGamepad(appId);
    if (!launched) {
      // Steam Apps surface unavailable — open the stream directly.
      window.open(
        `steam://openurl/https://www.xbox.com/play/launch/${gameId}`,
        "_blank",
      );
    }
  }, [appId, gameId]);

  return (
    <PlayShell>
      <DialogButton
        className="unifideck-play-btn"
        onClick={onPlay}
        style={actionBtnStyle}
      >
        <FaCloud /> {t("play.playOnCloud", "Play on Cloud")}
      </DialogButton>

      <MetaInline showLastPlayed appId={appId} />

      <IconGroup>
        <DialogButton
          style={iconBtnStyle}
          onClick={() => openControllerConfig(appId)}
          aria-label={t("playButton.controllerConfig")}
        >
          <FaGamepad />
        </DialogButton>
        <DialogButton
          style={iconBtnStyle}
          onClick={() => openAppSettings(appId)}
          aria-label={t("playButton.appSettings")}
        >
          <FaCog />
        </DialogButton>
      </IconGroup>
    </PlayShell>
  );
};
