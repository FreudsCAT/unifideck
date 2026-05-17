/**
 * GameInfoScores — Metacritic + ProtonDB + Deck Verified.
 *
 * Three score sources rendered side-by-side. ProtonDB tier and Deck
 * Verified status are sourced first from the in-memory compat cache
 * (populated by `LibraryContext` from `get_protondb_cache`) so the
 * pills appear synchronously when navigating to a game's details
 * page. Falls back to `get_game_metadata` if the cache is cold.
 */
import React, { FC, useEffect, useState } from "react";
import { Focusable } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useRPC } from "../../api/useRPC";
import { rpcRoutes } from "../../api/rpc-routes";
import {
  getCachedCompatByTitle,
  getCachedRating,
  type DeckVerifiedStatus,
  type ProtonDBTier,
} from "../../lib/protondb-cache";
import type { Game } from "../../types/api";

interface ScoresPayload {
  metacritic_score?: number;
  protondb_tier?: ProtonDBTier;
  deck_status?: DeckVerifiedStatus;
}

interface Props {
  game: Game;
}

const PROTON_TIER_COLOR: Record<string, string> = {
  platinum: "#e5e7eb",
  gold: "#facc15",
  silver: "#cbd5e1",
  bronze: "#a16207",
  borked: "#dc2626",
  native: "#86efac",
  pending: "#94a3b8",
};

const DECK_STATUS_COLOR: Record<DeckVerifiedStatus, string> = {
  verified: "#86efac",
  playable: "#fbbf24",
  unsupported: "#f87171",
  unknown: "#94a3b8",
};

function readFromCache(game: Game): ScoresPayload {
  const tier = game.app_id != null ? getCachedRating(game.app_id) : null;
  const byTitle = game.title ? getCachedCompatByTitle(game.title) : null;
  return {
    protondb_tier: (tier ?? byTitle?.tier ?? undefined) as ProtonDBTier | undefined,
    deck_status: byTitle?.deckVerified,
  };
}

export const GameInfoScores: FC<Props> = ({ game }) => {
  const { t } = useTranslation();
  // Backend signature is (store: str, store_game_id: str) — the
  // dataclass field name predates the TS rename to `id`.
  const fetchMeta = useRPC<[string, string], Game & ScoresPayload>(
    rpcRoutes.getGameMetadata,
  );
  const [scores, setScores] = useState<ScoresPayload | null>(() => readFromCache(game));

  useEffect(() => {
    setScores(readFromCache(game));
    if (!game.store || !game.id) return;
    fetchMeta(game.store, game.id).then(
      (full) => setScores((prev) => ({
        metacritic_score: full.metacritic_score,
        protondb_tier: full.protondb_tier ?? prev?.protondb_tier,
        deck_status: full.deck_status ?? prev?.deck_status,
      })),
      () => { /* keep the cached fallback */ },
    );
  }, [fetchMeta, game.store, game.id, game.title]);

  if (!scores
    || (!scores.metacritic_score && !scores.protondb_tier && !scores.deck_status)) {
    return null;
  }

  return (
    <Focusable
      flow-children="row"
      onActivate={() => {}}
      style={{ display: "flex", gap: 16, flexWrap: "wrap" }}
    >
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
            background: PROTON_TIER_COLOR[scores.protondb_tier] ?? "#94a3b8",
            color: "#0f172a", fontWeight: 600,
            textTransform: "uppercase",
          }}>
            {scores.protondb_tier}
          </div>
        </div>
      )}
      {scores.deck_status && scores.deck_status !== "unknown" && (
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8" }}>
            {t("info.deckVerified")}
          </div>
          <div style={{
            fontSize: 14, padding: "4px 10px", borderRadius: 4,
            background: DECK_STATUS_COLOR[scores.deck_status],
            color: "#0f172a", fontWeight: 600,
            textTransform: "uppercase",
          }}>
            {scores.deck_status}
          </div>
        </div>
      )}
    </Focusable>
  );
};
