/**
 * GameInfoPanel — top-level container for the game details
 * view in the App Details page.
 *
 * Replaces the 1232-line legacy GameInfoPanel which mixed
 * metadata fetch, view-mode logic, store actions, scores,
 * and artwork into one component. The container is now
 * ~120 LOC : it reads `useGameInfo`, picks compact vs full
 * via `useViewMode`, and renders three sub-sections.
 *
 * Empty / loading / error states are rendered inline
 * (single-purpose JSX, no extra components needed).
 */
import React, { FC } from "react";
import { Spinner } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useViewMode } from "../../hooks/useViewMode";
import { GameInfoHeader } from "./GameInfoHeader";
import { GameInfoMetadata } from "./GameInfoMetadata";
import { GameInfoScores } from "./GameInfoScores";

/** Props. */
interface Props {
  appId: number;
}

/**
 * Top-level panel rendered in place of Steam's native
 * game info for Unifideck shortcuts. Composes the Header,
 * Metadata and Scores sub-components and feeds them from
 * a single {@link useGameInfo} call to avoid duplicate
 * RPC traffic.
 */
export const GameInfoPanel: FC<Props> = ({ appId }) => {
  const { t } = useTranslation();
  const { data: game, loading, error } = useGameInfo(appId);
  const { mode } = useViewMode();
  if (loading) {
    return (
      <div style={{ padding: 16, textAlign: "center" }}>
        <Spinner />
      </div>
    );
  }
  if (error) {
    return (
      <div style={{ padding: 16, color: "#f87171" }}>
        {t("info.error", { message: error.message })}
      </div>
    );
  }
  if (!game) return null;
  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      gap: mode === "compact" ? 8 : 16,
      padding: 12,
    }}>
      <GameInfoHeader game={game} mode={mode} />
      <GameInfoMetadata game={game} mode={mode} />
      {mode === "full" && <GameInfoScores game={game} />}
    </div>
  );
};
