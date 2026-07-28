/**
 * GameInfoInfoRow — inline info card under the compatibility row.
 *
 * Renders `[StoreIcon] Title · Size · Cloud saves · Developer ·
 * Publisher · Released · Metacritic` in one wrap-friendly flex
 * row. Each cell is hidden when its value is empty / null, so a
 * non-Steam shortcut with no enrichment data collapses to just
 * the store icon and title.
 *
 * The cloud-saves cell is deliberately here rather than on the
 * install flow: it comes from cached catalog data, so it answers
 * "does THIS store's copy sync saves?" before the game is
 * downloaded — the deciding factor when the same title is owned
 * on both Epic and GOG.
 */
import { FC } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { FaCloud, FaTimes } from "react-icons/fa";
import { StoreIcon } from "../shared/StoreIcon";
import { useGameSize } from "../../hooks/useGameSize";
import type { Game, GameMetadata } from "../../types/api";

/** Stores with cloud-save sync — mirrors `useCloudSaveStatus`. */
const CLOUD_SAVE_STORES = new Set(["gog", "epic"]);

interface Props {
  appId: number;
  game: Game;
  meta: GameMetadata;
}

function formatSize(bytes?: number): string | null {
  if (!bytes) return null;
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} MB`;
}

function metacriticColor(score: number): string {
  if (score >= 75) return "#66cc33";
  if (score >= 50) return "#ffcc33";
  return "#ff0000";
}

const Cell: FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <span
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      fontSize: 13,
    }}
  >
    <strong style={{ color: "#8f98a0", fontWeight: 600 }}>{label}</strong>
    <span style={{ color: "#c7d5e0" }}>{children}</span>
  </span>
);

export const GameInfoInfoRow: FC<Props> = ({ appId, game, meta }) => {
  const { t } = useTranslation();
  // Fetched out-of-band so it never blocks the panel; fall back to any
  // size already on the game record. See useGameSize.
  const fetchedSize = useGameSize(appId, Boolean(game.is_installed));
  const size = formatSize(
    fetchedSize && fetchedSize > 0 ? fetchedSize : game.size_bytes,
  );
  // Only Epic and GOG have cloud-save sync, so a yes/no is only meaningful
  // there; elsewhere the absence of the cell is the honest answer. `null`
  // means the enriched catalog has no entry for this game — also silent,
  // rather than reporting "no cloud saves" we can't stand behind.
  const showCloudSaves =
    CLOUD_SAVE_STORES.has(game.store) &&
    meta.cloud_saves !== null &&
    meta.cloud_saves !== undefined;
  return (
    <Focusable
      flow-children="row"
      onActivate={() => {}}
      noFocusRing
      className="unifideck-game-info-row"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 16,
        background: "rgba(0, 0, 0, 0.3)",
        borderRadius: 6,
        padding: "12px 16px",
      }}
      onFocus={(e: React.FocusEvent<HTMLDivElement>) => {
        e.currentTarget.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <StoreIcon store={game.store} size={20} />
        <span style={{ fontWeight: 600, color: "#c7d5e0", fontSize: 14 }}>
          {game.title}
        </span>
      </span>
      {size && <Cell label={t("gameInfoPanel.labels.size")}>{size}</Cell>}
      {showCloudSaves && (
        // Rendered whether or not the game is installed — that is the point.
        // With the same title owned on both Epic and GOG, this is what tells
        // the user which copy actually syncs saves BEFORE they download one.
        <Cell label={t("gameInfoPanel.labels.cloudSaves")}>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              color: meta.cloud_saves ? "#66cc33" : "#8f98a0",
            }}
          >
            {meta.cloud_saves ? <FaCloud size={12} /> : <FaTimes size={12} />}
            {meta.cloud_saves
              ? t("gameInfoPanel.cloudSaves.supported")
              : t("gameInfoPanel.cloudSaves.unsupported")}
          </span>
        </Cell>
      )}
      {meta.developer && (
        <Cell label={t("gameInfoPanel.labels.developer")}>
          {meta.developer}
        </Cell>
      )}
      {meta.publisher && (
        <Cell label={t("gameInfoPanel.labels.publisher")}>
          {meta.publisher}
        </Cell>
      )}
      {meta.release_date && (
        <Cell label={t("gameInfoPanel.labels.released")}>
          {meta.release_date}
        </Cell>
      )}
      {meta.metacritic != null && (
        <Cell label={t("gameInfoPanel.labels.metacritic")}>
          <span
            style={{
              padding: "2px 8px",
              borderRadius: 3,
              background: metacriticColor(meta.metacritic),
              color: "#000000",
              fontWeight: 700,
              fontSize: 12,
            }}
          >
            {meta.metacritic}
          </span>
        </Cell>
      )}
    </Focusable>
  );
};
