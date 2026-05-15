/**
 * useInstallFlow — orchestrates the install handshake.
 *
 * Wraps `useGameActions.install` with the per-store side
 * quests the user may have to walk through before the
 * download is queued :
 *  - GOG: fetch `get_gog_game_languages`, prompt the user via
 *    `<GOGLanguageSelectModal>` when more than one is offered.
 *  - Other stores: pass straight through.
 *
 * Keeps decision logic OUT of `NotInstalledButtons.tsx`, which
 * the PDF mandates be pure presentation. The component just
 * calls `installFlow.start(game)` and the hook handles the
 * fork.
 */
import { useCallback, useState } from "react";
import { showModal } from "@decky/ui";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useGameActions } from "./useGameActions";
import { GOGLanguageSelectModal } from "../components/modals/GOGLanguageSelectModal";
import type { Game, Result } from "../types/api";

/** Steam bridge shape — same minimal surface useGameActions
 *  consumes. */
interface SteamBridgeShape {
  runGame(appId: string, launchOptions: string): void;
  terminateApp(appId: string, force?: boolean): void;
}

/** Languages response from `get_gog_game_languages`. The
 *  backend returns the raw locale codes ; the modal maps
 *  them to display labels. */
interface GogLanguagesResponse {
  success: boolean;
  languages: string[];
}

/**
 * Bundle returned by {@link useInstallFlow} — `start(game)`
 * kicks the install and resolves with the RPC result so the
 * caller can toast success / failure.
 */
export interface UseInstallFlowResult {
  isWorking: boolean;
  start: (game: Game) => Promise<Result | null>;
}

/**
 * Wraps install with the per-store prompts required to
 * complete the handshake. Returns `null` if the user
 * cancels a prompt.
 */
export function useInstallFlow(bridge: SteamBridgeShape): UseInstallFlowResult {
  const actions = useGameActions(bridge);
  const getGogLangs = useRPC<[string], GogLanguagesResponse>(
    rpcRoutes.getGogGameLanguages,
  );
  const [working, setWorking] = useState(false);

  const start = useCallback(
    async (game: Game): Promise<Result | null> => {
      setWorking(true);
      try {
        if (game.store !== "gog") {
          return await actions.install(game.store, game.id);
        }
        const langs = await getGogLangs(game.id).catch(() => null);
        const list = langs?.languages ?? [];
        if (list.length <= 1) {
          const language = list[0];
          return await actions.install(game.store, game.id, { language });
        }
        const language = await pickLanguageViaModal(game.title, list);
        if (!language) return null;
        return await actions.install(game.store, game.id, { language });
      } finally {
        setWorking(false);
      }
    },
    [actions, getGogLangs],
  );

  return { isWorking: working || actions.isWorking, start };
}

/** Promise-wrapped showModal that resolves with the picked
 *  language (or null on cancel). */
function pickLanguageViaModal(
  title: string,
  languages: string[],
): Promise<string | null> {
  return new Promise((resolve) => {
    let confirmed = false;
    const handle = showModal(
      <GOGLanguageSelectModal
        gameTitle={title}
        languages={languages}
        onConfirm={(lang) => {
          confirmed = true;
          resolve(lang);
        }}
        closeModal={() => {
          handle?.Close();
          if (!confirmed) resolve(null);
        }}
      />,
    );
  });
}
