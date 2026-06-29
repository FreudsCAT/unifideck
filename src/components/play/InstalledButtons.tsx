/**
 * InstalledButtons — Play section variant for an installed
 * Unifideck game.
 *
 * Single horizontal row inside {@link PlayShell} :
 *
 *   Idle:    [ Play ]     Space Required · Last Played      [ 🎮 ] [ ⚙ ] [ ✕ ]
 *   Running: [ Resume ] [ ✕ ]   Space Required · Last Played [ 🎮 ] [ ⚙ ] [ ✕ ]
 *   Update:  [ Update ]  Space Required · Last Played      [ 🎮 ] [ ⚙ ] [ ✕ ]
 *
 * Running detection polls Steam's per-client ``display_status``
 * every 2 s (4 = running, 1 = launching). Update detection
 * fires ``check_game_update`` once on mount.
 */
import { FC, useCallback, useEffect, useState } from "react";
import { DialogButton, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { call } from "@decky/api";
import {
  FaCog,
  FaGamepad,
  FaPlay,
  FaSyncAlt,
  FaTimes,
  FaTrash,
} from "react-icons/fa";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameActions } from "../../hooks/useGameActions";
import { useLaunchPrep } from "../../hooks/useLaunchPrep";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import { openNativeAppManageMenu } from "../../utils/nativeAppMenu";
import { UninstallConfirmModal } from "../modals/UninstallConfirmModal";
import { CloudSaveButton } from "./CloudSaveButton";
import {
  PlayShell,
  MetaInline,
  IconGroup,
  actionBtnStyle,
  iconBtnStyle,
} from "./PlayMeta";

interface Props {
  appId: number;
  bridge?: SteamBridge;
}

const defaultBridge = new SteamBridge();
const RUNNING_POLL_MS = 2000;
const STEAM_STATUS_RUNNING = 4;
const STEAM_STATUS_LAUNCHING = 1;

function readDisplayStatus(appId: number): number | undefined {
  const store = (
    window as unknown as {
      appStore?: {
        m_mapApps?: {
          get?: (
            id: number,
          ) =>
            | { local_per_client_data?: { display_status?: number } }
            | undefined;
        };
      };
    }
  ).appStore;
  const app = store?.m_mapApps?.get?.(appId);
  return app?.local_per_client_data?.display_status;
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

interface UpdateCheckResponse {
  has_update?: boolean;
}

export const InstalledButtons: FC<Props> = ({
  appId,
  bridge = defaultBridge,
}) => {
  const { t } = useTranslation();
  const { data: game, loading } = useGameInfo(appId);
  const actions = useGameActions(bridge);
  const toast = useToast();
  const [isRunning, setIsRunning] = useState(false);
  const [hasUpdate, setHasUpdate] = useState(false);

  // Clear Steam's Force-Compatibility while this page is open so
  // RunGame runs our launcher natively (the launcher applies Proton
  // itself); restored on page-leave. See useLaunchPrep.
  useLaunchPrep(appId, game);

  // Running-state poll (2 s).
  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      const status = readDisplayStatus(appId);
      if (status === undefined) return;
      setIsRunning(
        status === STEAM_STATUS_RUNNING || status === STEAM_STATUS_LAUNCHING,
      );
    };
    tick();
    const id = window.setInterval(tick, RUNNING_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [appId]);

  // Update check — one-shot on mount. The backend RPC takes
  // (store, game_id) and returns { has_update } — passing the raw
  // appId (the old call) threw "missing argument: game_id" on every
  // page load and update detection never worked.
  useEffect(() => {
    if (!game) return;
    let cancelled = false;
    call<[string, string], UpdateCheckResponse>(
      "check_game_update",
      game.store,
      game.id,
    )
      .then((res) => {
        if (cancelled) return;
        setHasUpdate(Boolean(res?.has_update));
      })
      .catch(() => {
        /* non-critical */
      });
    return () => {
      cancelled = true;
    };
  }, [game]);

  const onPlay = useCallback(() => {
    if (!game) return;
    actions.launch(appId);
  }, [actions, appId, game]);

  const onStop = useCallback(() => {
    actions.terminate(appId);
  }, [actions, appId]);

  const onUpdate = useCallback(async () => {
    if (!game) return;
    try {
      const res = await call<[number], { success: boolean; error?: string }>(
        "update_game",
        appId,
      );
      if (res?.success) {
        setHasUpdate(false);
        toast.success(t("toasts.updateQueued"));
      } else {
        toast.error(t("toasts.updateFailed"), res?.error ?? "");
      }
    } catch (e) {
      toast.error(t("toasts.updateFailed"), String(e));
    }
  }, [appId, game, t, toast]);

  const onUninstall = useCallback(() => {
    if (!game) return;
    showModal(
      <UninstallConfirmModal
        gameId={appId}
        gameTitle={game.title}
        onConfirm={async (deletePrefix) => {
          const r = await actions.uninstall(appId, deletePrefix);
          if (r?.success) toast.success(t("toasts.uninstallDone"));
        }}
        closeModal={() => {}}
      />,
    );
  }, [actions, appId, game, t, toast]);

  const primaryButtons = (() => {
    if (isRunning) {
      return (
        <>
          <DialogButton
            className="unifideck-resume-btn"
            disabled={loading}
            onClick={onPlay}
            style={actionBtnStyle}
          >
            <FaPlay /> {t("play.resume")}
          </DialogButton>
          <DialogButton
            className="unifideck-stop-btn"
            onClick={onStop}
            style={iconBtnStyle}
            aria-label={t("play.stop")}
          >
            <FaTimes />
          </DialogButton>
        </>
      );
    }
    if (hasUpdate) {
      return (
        <DialogButton
          className="unifideck-update-btn"
          disabled={loading || actions.isWorking}
          onClick={onUpdate}
          style={actionBtnStyle}
        >
          <FaSyncAlt /> {t("play.update")}
        </DialogButton>
      );
    }
    return (
      <DialogButton
        className="unifideck-play-btn"
        disabled={loading}
        onClick={onPlay}
        style={actionBtnStyle}
      >
        <FaPlay /> {t("play.play")}
      </DialogButton>
    );
  })();

  return (
    <PlayShell autoFocus>
      {primaryButtons}
      <MetaInline
        sizeBytes={game?.size_bytes}
        showLastPlayed
        appId={appId}
        store={game?.store}
        gameId={game?.id}
        installed
      />
      <IconGroup>
        {game && (
          <CloudSaveButton
            store={game.store}
            gameId={game.id}
            gameTitle={game.title}
          />
        )}
        <DialogButton
          className="unifideck-icon-btn"
          style={iconBtnStyle}
          onClick={() => openControllerConfig(appId)}
          aria-label={t("playButton.controllerConfig")}
        >
          <FaGamepad />
        </DialogButton>
        <DialogButton
          className="unifideck-icon-btn"
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
          <FaCog />
        </DialogButton>
        <DialogButton
          className="unifideck-icon-btn"
          style={iconBtnStyle}
          disabled={loading || actions.isWorking}
          onClick={onUninstall}
          aria-label={t("play.uninstall")}
        >
          <FaTrash />
        </DialogButton>
      </IconGroup>
    </PlayShell>
  );
};
