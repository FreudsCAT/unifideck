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
import { FaPlay, FaSyncAlt, FaTimes, FaTrash } from "react-icons/fa";
import { SteamControllerIcon, SteamGearIcon } from "../shared";
import { useRPC } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameActions } from "../../hooks/useGameActions";
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
  actionBtnClass,
  iconBtnClass,
  controllerBtnClass,
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
  const checkGameUpdate = useRPC<[string, string], UpdateCheckResponse>(
    rpcRoutes.checkGameUpdate,
  );
  // Depend on the identifiers, not the ``Game`` object: ``useGameInfo`` is
  // stale-while-revalidate on a 5 s TTL, so every background refresh mints a
  // new object identity and would otherwise re-fire the check for the same
  // game (and, before the backend cache, a fresh legendary login with it).
  const gameStore = game?.store;
  const gameId = game?.id;

  // NOTE: we deliberately do NOT touch Steam's Force-Compatibility here.
  // This used to capture it into proton_settings.json and clear it so
  // RunGame wouldn't wrap our launcher in Proton — but clearing it meant
  // the launcher could never read the user's ACTUAL selection at launch
  // time (only a copy that went stale whenever the capture/restore dance
  // was interrupted), so switching Proton in Steam's dialog appeared to do
  // nothing for some games and work for others purely by timing.
  // ``config.vdf``'s CompatToolMapping is now the single source of truth,
  // read by ``selector.select_proton_version``; the double-Proton problem
  // the clearing existed to avoid is handled properly at the umu spawn
  // point by ``launcher.proton.infrastructure.container_escape``.

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
  // (store, game_id) and returns { has_update }.
  //
  // This MUST go through ``useRPC``, not a bare ``call()``. Every backend
  // method is wrapped by ``@auto_wrap_rpc_methods``, so a return value with
  // no top-level ``success`` key comes back nested:
  //   {"has_update": true} -> {success: true, error: null, data: {has_update: true}}
  // Reading ``res.has_update`` off that envelope is always ``undefined``,
  // which is why the Update button never appeared for ANY store. ``useRPC``
  // unwraps the envelope (and throws on a failed one — the catch below
  // fails open to "no update", which is the right default).
  useEffect(() => {
    if (!gameStore || !gameId) return;
    let cancelled = false;
    checkGameUpdate(gameStore, gameId)
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
  }, [checkGameUpdate, gameStore, gameId]);

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
      // Deliberately a raw ``call`` rather than ``useRPC``: this handler
      // wants to SHOW the failure reason in a toast, and ``useRPC`` throws
      // on a non-success envelope. ``update_game`` returns a dict that
      // already carries ``success``, so the wrapper keeps both keys at the
      // top level of the envelope — reading them here is correct.
      const res = await call<[number], { success: boolean; error?: string }>(
        rpcRoutes.updateGame,
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
            className={actionBtnClass("unifideck-resume-btn")}
            disabled={loading}
            onClick={onPlay}
            style={actionBtnStyle}
          >
            <FaPlay /> {t("play.resume")}
          </DialogButton>
          <DialogButton
            className={iconBtnClass("unifideck-stop-btn")}
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
          className={actionBtnClass("unifideck-update-btn")}
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
        className={actionBtnClass("unifideck-play-btn")}
        disabled={loading}
        onClick={onPlay}
        style={actionBtnStyle}
      >
        <FaPlay /> {t("play.play")}
      </DialogButton>
    );
  })();

  return (
    // autoFocus is intentional: claims gamepad focus for the primary action
    // eslint-disable-next-line jsx-a11y/no-autofocus
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
        <DialogButton
          className={iconBtnClass()}
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
