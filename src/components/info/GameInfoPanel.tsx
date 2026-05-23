/**
 * GameInfoPanel — top-level container for the game details
 * view in the App Details page.
 *
 * Composes the modular sub-components:
 *  - Header (cover + title + store + view-mode toggle)
 *  - CompatRow (compat badge + Details / Synopsis / Install + genres)
 *  - InfoRow (size · developer · publisher · released · metacritic)
 *  - Metadata (install_path · executable · deck rating)
 *  - SynopsisSection (collapsible description, gated on the toggle)
 *  - NavButtons (Steam navigation + per-store Support)
 *  - Scores (ProtonDB tier + Deck Verified status)
 *
 * Compact view mode renders only the Header, matching the
 * existing settings toggle semantics. Full view mode renders
 * everything, but only the metadata-dependent sections wait
 * for {@link useGameMetadata} — install state via
 * {@link useGameInfo} drives Header / Metadata immediately.
 */
import { FC, useState } from "react";
import { Focusable, Spinner } from "@decky/ui";
import { useTranslation } from "react-i18next";
import { useGameInfo } from "../../hooks/useGameInfo";
import { useGameMetadata } from "../../hooks/useGameMetadata";
import { useViewMode } from "../../hooks/useViewMode";
import { GameInfoHeader } from "./GameInfoHeader";
import { GameInfoCompatRow } from "./GameInfoCompatRow";
import { GameInfoInfoRow } from "./GameInfoInfoRow";
import { GameInfoMetadata } from "./GameInfoMetadata";
import { GameInfoSynopsisSection } from "./GameInfoSynopsisSection";
import { GameInfoNavButtons } from "./GameInfoNavButtons";
import { GameInfoScores } from "./GameInfoScores";

interface Props {
  appId: number;
}

/** Focus-state CSS shared by every interactive element in the
 *  panel. Injected once at panel mount because the panel is
 *  rendered into Steam's spliced React tree — there is no app-
 *  level <head> we can collocate this with. */
const PANEL_FOCUS_CSS = `
@keyframes unifideck-focus-breathe {
  0%, 100% { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.35); }
  50%      { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.7); }
}
.unifideck-nav-button.gpfocus,
.unifideck-nav-button:hover {
  animation: unifideck-focus-breathe 1.5s ease-in-out infinite;
}
.unifideck-install-button.install-state   { background-color: #1a9fff; }
.unifideck-install-button.uninstall-state { background-color: #d32f2f; }
.unifideck-install-button.cancel-state    { background-color: #d32f2f; }
.unifideck-game-info-row.gpfocus,
.unifideck-synopsis-section.gpfocus {
  animation: unifideck-focus-breathe 1.5s ease-in-out infinite;
}
`;

export const GameInfoPanel: FC<Props> = ({ appId }) => {
  const { t } = useTranslation();
  const { data: game, loading, error } = useGameInfo(appId);
  const { data: meta } = useGameMetadata(appId);
  const { mode } = useViewMode();
  const [synopsisOpen, setSynopsisOpen] = useState(false);

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
    <>
      <style>{PANEL_FOCUS_CSS}</style>
      <Focusable
        flow-children="column"
        onActivate={() => {}}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: mode === "compact" ? 8 : 16,
          padding: 12,
        }}
      >
        <GameInfoHeader game={game} mode={mode} />
        {mode === "full" && (
          <>
            {meta && (
              <GameInfoCompatRow
                appId={appId}
                meta={meta}
                synopsisOpen={synopsisOpen}
                onToggleSynopsis={() => setSynopsisOpen((v) => !v)}
              />
            )}
            {meta && <GameInfoInfoRow game={game} meta={meta} />}
            <GameInfoMetadata game={game} mode={mode} />
            {synopsisOpen && meta?.description && (
              <GameInfoSynopsisSection description={meta.description} />
            )}
            {meta && <GameInfoNavButtons meta={meta} />}
            <GameInfoScores game={game} />
          </>
        )}
      </Focusable>
    </>
  );
};
