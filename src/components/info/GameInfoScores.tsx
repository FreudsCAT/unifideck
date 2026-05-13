/**
 * GameInfoScores — Metacritic + ProtonDB scores block.
 *
 * Fetches scores via `get_game_metadata` (extension fields
 * `metacritic_score`, `protondb_tier`). Renders nothing if
 * neither score is available — keeps the panel uncluttered
 * for indie games and obscure titles that score sources
 * don't cover.
 */
import React, { FC, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRPC } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import type { Game } from "../../types/api";

/** Scores payload. */
interface ScoresPayload {
  metacritic_score?: number;
  protondb_tier?: "platinum" | "gold" | "silver" | "bronze" | "borked";
}

/** Props. */
interface Props {
  game: Game;
}

const PROTON_TIER_COLOR: Record<string, string> = {
  platinum: "#e5e7eb",
  gold: "#facc15",
  silver: "#cbd5e1",
  bronze: "#a16207",
  borked: "#dc2626",
};

/**
 * Scores block of the game-info panel : Metacritic,
 * ProtonDB rating, Deck Verified status. Each score is
 * resolved lazily and skipped if the source is offline.
 */
export const GameInfoScores: FC<Props> = ({ game }) => {
  const { t } = useTranslation();
  const fetchMeta = useRPC<[number], Game & ScoresPayload>(
    rpcRoutes.getGameMetadata,
  );
  const [scores, setScores] = useState<ScoresPayload | null>(null);
  useEffect(() => {
    if (game.app_id == null) return;
    fetchMeta(game.app_id).then(
      (full) => {
        setScores({
          metacritic_score: full.metacritic_score,
          protondb_tier: full.protondb_tier,
        });
      },
      () => setScores(null),
    );
  }, [fetchMeta, game.app_id]);
  if (!scores || (!scores.metacritic_score && !scores.protondb_tier)) {
    return null;
  }
  return (
    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
      {scores.metacritic_score != null && (
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8" }}>
            {t("info.metacritic")}
          </div>
          <div style={{ fontSize: 22, fontWeight: 600 }}>
            {scores.metacritic_score}
          </div>
        </div>
      )}
      {scores.protondb_tier && (
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8" }}>
            {t("info.protonDb")}
          </div>
          <div style={{
            fontSize: 14, padding: "4px 10px", borderRadius: 4,
            background: PROTON_TIER_COLOR[scores.protondb_tier],
            color: "#0f172a", fontWeight: 600,
            textTransform: "uppercase",
          }}>
            {scores.protondb_tier}
          </div>
        </div>
      )}
    </div>
  );
};
