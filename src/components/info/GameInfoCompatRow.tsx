/**
 * GameInfoCompatRow — top action row of the game info panel.
 *
 * Renders the Steam Deck compatibility badge, the Details and
 * Synopsis toggle buttons, the Install / Uninstall / Cancel
 * action button (live progress when downloading), and the
 * comma-dot-separated genre tags underneath.
 *
 * The action button drives:
 *  - install via {@link useInstallFlow} (handles GOG language picker);
 *  - uninstall via {@link useGameActions} + {@link UninstallConfirmModal};
 *  - cancel via {@link useGameActions} + a {@link ConfirmModal} dialog.
 *
 * Live progress comes from the {@link useDownloads} queue snapshot
 * — no per-second polling.
 */
import { FC, useCallback, useMemo } from "react";
import { ConfirmModal, DialogButton, Focusable, showModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameActions } from "../../hooks/useGameActions";
import { useInstallFlow } from "../../hooks/useInstallFlow";
import { useDownloads } from "../../contexts/DownloadContext";
import { useToast } from "../../hooks/useToast";
import { SteamBridge } from "../../lib/steam-bridge";
import { UninstallConfirmModal } from "../modals/UninstallConfirmModal";
import { GameInfoDetailsModal } from "./GameInfoDetailsModal";
import type { GameMetadata } from "../../types/api";

interface Props {
  appId: number;
  meta: GameMetadata;
  synopsisOpen: boolean;
  onToggleSynopsis: () => void;
  bridge?: SteamBridge;
}

const COMPAT_COLORS: Record<0 | 1 | 2 | 3, { bg: string; fg: string }> = {
  3: { bg: "#59bf40", fg: "#ffffff" },
  2: { bg: "#ffc82c", fg: "#000000" },
  1: { bg: "#ff4444", fg: "#ffffff" },
  0: { bg: "#666666", fg: "#ffffff" },
};

const COMPAT_LABEL_KEY: Record<0 | 1 | 2 | 3, string> = {
  3: "gameInfoPanel.compatibility.verified",
  2: "gameInfoPanel.compatibility.playable",
  1: "gameInfoPanel.compatibility.unsupported",
  0: "gameInfoPanel.compatibility.unknown",
};

/** Inline-flex sizing for every DialogButton in the panel.
 *  @decky/ui's DialogButton defaults to ``width: 100%`` (settings-
 *  menu styling) — without ``flex: 0 0 auto`` the buttons stretch
 *  to fill their parent flex line. */
const buttonStyle = {
  padding: "4px 12px",
  fontSize: 12,
  minWidth: 0,
  width: "auto",
  flex: "0 0 auto",
  display: "inline-flex",
  alignItems: "center",
} as const;

const defaultBridge = new SteamBridge();

export const GameInfoCompatRow: FC<Props> = ({
  appId, meta, synopsisOpen, onToggleSynopsis, bridge = defaultBridge,
}) => {
  const { t } = useTranslation();
  const { data: game } = useGameInfo(appId);
  const downloads = useDownloads();
  const installFlow = useInstallFlow(bridge);
  const actions = useGameActions(bridge);
  const toast = useToast();

  const activeDownload = useMemo(() => {
    if (!game || !downloads.queue) return null;
    const all = [downloads.queue.current, ...downloads.queue.queued];
    return all.find(
      (d) => d != null && d.store === game.store && d.game_id === game.id,
    ) ?? null;
  }, [downloads.queue, game]);

  const isDownloading = activeDownload != null;
  const progress = activeDownload?.progress_percent ?? 0;

  const showInstallConfirm = useCallback(() => {
    if (!game) return;
    showModal(
      <ConfirmModal
        strTitle={t("confirmModals.installTitle")}
        strDescription={t("confirmModals.installDescription", { title: game.title })}
        strOKButtonText={t("confirmModals.yes")}
        strCancelButtonText={t("confirmModals.no")}
        onOK={async () => {
          const r = await installFlow.start(game);
          if (r?.success) toast.success(t("toasts.downloadStarted"));
          else if (r) toast.error(t("toasts.downloadFailed"));
        }}
      />,
    );
  }, [game, installFlow, t, toast]);

  const showUninstallConfirm = useCallback(() => {
    if (!game) return;
    showModal(
      <UninstallConfirmModal
        gameId={appId}
        gameTitle={game.title}
        onConfirm={async () => {
          const r = await actions.uninstall(appId);
          if (r?.success) toast.success(t("toasts.uninstallComplete"));
        }}
        closeModal={() => {}}
      />,
    );
  }, [actions, appId, game, t, toast]);

  const showCancelConfirm = useCallback(() => {
    if (!activeDownload) return;
    showModal(
      <ConfirmModal
        strTitle={t("confirmModals.cancelTitle")}
        strDescription={t("confirmModals.cancelDescription", {
          title: game?.title ?? t("common.thisGame"),
        })}
        strOKButtonText={t("confirmModals.yes")}
        strCancelButtonText={t("confirmModals.no")}
        onOK={async () => {
          const r = await actions.cancel(activeDownload.id);
          if (r?.success) toast.success(t("toasts.downloadCancelled"));
          else if (r) toast.error(t("toasts.cancelFailed"));
        }}
      />,
    );
  }, [activeDownload, actions, game, t, toast]);

  const onAction = useCallback(() => {
    if (isDownloading) showCancelConfirm();
    else if (game?.is_installed) showUninstallConfirm();
    else showInstallConfirm();
  }, [isDownloading, game, showCancelConfirm, showUninstallConfirm, showInstallConfirm]);

  const onDetails = useCallback(() => {
    showModal(<GameInfoDetailsModal meta={meta} closeModal={() => {}} />);
  }, [meta]);

  const actionState = isDownloading ? "cancel" : game?.is_installed ? "uninstall" : "install";
  const actionLabel = isDownloading
    ? `${t("gameInfoPanel.buttons.cancel")} (${Math.round(progress)}%)`
    : game?.is_installed
      ? t("gameInfoPanel.buttons.uninstall")
      : t("gameInfoPanel.buttons.install");

  // Microsoft games tagged "not_compatible" can't install through us —
  // hide the button to avoid suggesting they can.
  const showAction = !!game && !(
    game.store === "microsoft"
    && (game.store_tags ?? []).includes("not_compatible" as never)
  );

  const compatColors = COMPAT_COLORS[meta.deck_compatibility];

  return (
    <Focusable
      flow-children="row"
      onActivate={() => {}}
      onFocus={(e: React.FocusEvent<HTMLDivElement>) => {
        e.currentTarget.scrollIntoView({ behavior: "smooth", block: "center" });
      }}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <span style={{
          padding: "4px 12px",
          borderRadius: 4,
          background: compatColors.bg,
          color: compatColors.fg,
          fontWeight: 700,
          fontSize: 12,
          letterSpacing: 0.5,
          textTransform: "uppercase",
        }}>
          {t(COMPAT_LABEL_KEY[meta.deck_compatibility])}
        </span>
        <DialogButton
          className="unifideck-nav-button"
          style={buttonStyle}
          onClick={onDetails}
        >
          {t("gameInfoPanel.buttons.details")}
        </DialogButton>
        {meta.description && (
          <DialogButton
            className="unifideck-nav-button"
            style={{
              ...buttonStyle,
              ...(synopsisOpen
                ? { background: "#1a9fff", color: "#ffffff" }
                : null),
            }}
            onClick={onToggleSynopsis}
          >
            {t("gameInfoPanel.buttons.synopsis")}
          </DialogButton>
        )}
        {showAction && (
          <DialogButton
            className={`unifideck-nav-button unifideck-install-button ${actionState}-state`}
            style={buttonStyle}
            disabled={installFlow.isWorking || actions.isWorking}
            onClick={onAction}
          >
            {actionLabel}
          </DialogButton>
        )}
      </div>
      {meta.genres.length > 0 && (
        <div style={{ color: "#8f98a0", fontSize: 13 }}>
          {meta.genres.join(" • ")}
        </div>
      )}
    </Focusable>
  );
};
