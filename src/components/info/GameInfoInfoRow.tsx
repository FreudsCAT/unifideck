/**
 * GameInfoInfoRow — inline info card under the compatibility row.
 *
 * Renders `[StoreIcon] Title · Size · Developer · Publisher ·
 * Released · Metacritic` in one wrap-friendly flex row. Each
 * cell is hidden when its value is empty / null, so a non-Steam
 * shortcut with no enrichment data collapses to just the store
 * icon and title.
 */
import { FC } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { StoreIcon } from "../shared/StoreIcon";
import type { Game, GameMetadata } from "../../types/api";

interface Props {
  game: Game;
  meta: GameMetadata;
}

function formatSize(bytes?: number): string | null {
  if (!bytes) return null;
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 ** 2);
  return `${mb.toFixed(0)} MB`;
}

function metacriticColor(score: number): string {
  if (score >= 75) return "#66cc33";
  if (score >= 50) return "#ffcc33";
  return "#ff0000";
}

const Cell: FC<{ label: string; children: React.ReactNode }> = (
  { label, children },
) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13 }}>
    <strong style={{ color: "#8f98a0", fontWeight: 600 }}>{label}</strong>
    <span style={{ color: "#c7d5e0" }}>{children}</span>
  </span>
);

export const GameInfoInfoRow: FC<Props> = ({ game, meta }) => {
  const { t } = useTranslation();
  const size = formatSize(game.size_bytes);
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
        e.currentTarget.scrollIntoView({ behavior: "smooth", block: "center" });
      }}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <StoreIcon store={game.store} size={20} />
        <span style={{ fontWeight: 600, color: "#c7d5e0", fontSize: 14 }}>
          {game.title}
        </span>
      </span>
      {size && <Cell label={t("gameInfoPanel.labels.size")}>{size}</Cell>}
      {meta.developer && (
        <Cell label={t("gameInfoPanel.labels.developer")}>{meta.developer}</Cell>
      )}
      {meta.publisher && (
        <Cell label={t("gameInfoPanel.labels.publisher")}>{meta.publisher}</Cell>
      )}
      {meta.release_date && (
        <Cell label={t("gameInfoPanel.labels.released")}>{meta.release_date}</Cell>
      )}
      {meta.metacritic != null && (
        <Cell label={t("gameInfoPanel.labels.metacritic")}>
          <span style={{
            padding: "2px 8px",
            borderRadius: 3,
            background: metacriticColor(meta.metacritic),
            color: "#000000",
            fontWeight: 700,
            fontSize: 12,
          }}>
            {meta.metacritic}
          </span>
        </Cell>
      )}
    </Focusable>
  );
};
