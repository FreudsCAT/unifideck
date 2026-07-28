/**
 * DownloadsTab — download activity plus the installed-game list, inside the
 * QuickAccess panel.
 *
 * Four sections: "Now downloading", "Queued", "Installed", "Failed".
 *
 * The Installed section is derived from real install state
 * (``get_all_unifideck_games`` filtered on the backend's ``installed`` flag),
 * NOT from the download history. The tab used to show only the last handful
 * of downloads this plugin had performed, so games installed before the
 * plugin — or beyond the history cap — never appeared, and users resorted to
 * reinstalling a game just to make it show up. Deriving from install state
 * also means an uninstalled game simply leaves the list.
 *
 * Successful/cancelled history rows are gone with it: once the list reflects
 * what is actually installed, "this finished a while ago" is noise. Failures
 * stay, because a failure is the one outcome the user still needs to see —
 * and they can be dismissed once read.
 *
 * Live updates come from EventBus via `useDownloads()` for the queue, and
 * from GAME_INSTALLED / GAME_UNINSTALLED for the installed list.
 */
import { FC, useCallback, useMemo } from "react";
import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useDownloads } from "../../contexts/DownloadContext";
import { useRPCQuery, useRPCMutation } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import { useEventBus } from "../../api/event-bus-client";
import { Events } from "../../types/events";
import { DownloadItemRow } from "./DownloadItemRow";
import { InstalledGameRow } from "./InstalledGameRow";
import { PLAY_FOCUS_CSS } from "../play/play.css";
import type { Game } from "../../types/api";

/**
 * Quick Access Menu tab: active downloads, the installed library, and any
 * failures worth surfacing.
 */
export const DownloadsTab: FC = () => {
  const { t } = useTranslation();
  const { queue, loading, refresh } = useDownloads();
  const games = useRPCQuery<[], Game[]>(rpcRoutes.getAllUnifideckGames, []);
  const clearHistory = useRPCMutation<[string | null], unknown>(
    rpcRoutes.clearDownloadHistory,
  );

  // Install state changes without a library sync, so refetch on the bus
  // events rather than waiting for `unifideck-sync-completed`.
  const refetchGames = useCallback(() => {
    void games.refetch();
  }, [games]);
  useEventBus(Events.GAME_INSTALLED, refetchGames, []);
  useEventBus(Events.GAME_UNINSTALLED, refetchGames, []);

  const installed = useMemo(() => {
    // Raw RPC rows carry the wire field `installed`; only adapter-normalised
    // rows have `is_installed`. Read both (see the note on `Game`).
    const rows = (games.data ?? []).filter(
      (g) => g.installed ?? g.is_installed,
    );
    return [...rows].sort((a, b) => a.title.localeCompare(b.title));
  }, [games.data]);

  if (loading || !queue) return null;
  // Defensive : backend may omit any of these keys on early
  // boot or partial-failure responses. Treat missing as empty.
  const current = queue.current ?? null;
  const queued = queue.queued ?? [];
  // Only failures survive in the history view now — completions are
  // represented by the game appearing in "Installed".
  const failed = (queue.finished ?? []).filter((i) => i.status === "failed");

  const empty =
    !current &&
    queued.length === 0 &&
    failed.length === 0 &&
    installed.length === 0;
  if (empty) {
    return (
      <PanelSection title={t("downloads.title")}>
        <div style={{ padding: 16, textAlign: "center", color: "#94a3b8" }}>
          {t("downloads.empty")}
        </div>
      </PanelSection>
    );
  }
  return (
    <>
      {/* Inline so the button/badge focus rules land in the QuickAccess
          CEF document (separate from the App-Details one). */}
      <style>{PLAY_FOCUS_CSS}</style>
      {current && (
        <PanelSection title={t("downloads.current")}>
          <DownloadItemRow item={current} variant="current" />
        </PanelSection>
      )}
      {queued.length > 0 && (
        <PanelSection title={t("downloads.queued")}>
          {queued.map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="queued" />
          ))}
        </PanelSection>
      )}
      {installed.length > 0 && (
        <PanelSection
          title={t("downloads.installedCount", { count: installed.length })}
        >
          {installed.map((game) => (
            <InstalledGameRow
              key={`${game.store}:${game.id}`}
              game={game}
              onUninstalled={refetchGames}
            />
          ))}
        </PanelSection>
      )}
      {failed.length > 0 && (
        <PanelSection title={t("downloads.failed")}>
          {failed.map((item) => (
            <DownloadItemRow key={item.id} item={item} variant="finished" />
          ))}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={clearHistory.loading}
              // Clearing history emits no bus event, so pull the queue
              // snapshot back explicitly or the rows linger until the next
              // poll.
              onClick={() => {
                void clearHistory.mutate(null).then(refresh);
              }}
            >
              {t("downloads.clearFailed")}
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
};
