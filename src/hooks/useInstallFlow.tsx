/**
 * useInstallFlow — orchestrates the install handshake.
 *
 * Wraps `useGameActions.install` with the per-store side
 * quests the user may have to walk through before the
 * download is queued :
 *  - Storage picker: `pickStorageForInstall` always runs first
 *    so the user chooses where the game lands.
 *  - GOG: fetch `get_gog_game_languages`, prompt the user via
 *    `<GOGLanguageSelectModal>` when more than one is offered.
 *  - Other stores: pass straight through.
 */
import { useCallback, useState } from "react";
import { showModal } from "@decky/ui";
import i18n from "i18next";
import { useRPC } from "../api/useRPC";
import { rpcRoutes } from "../api/rpc-routes";
import { useGameActions } from "./useGameActions";
import { useStorageConfig } from "./useStorageConfig";
import { GOGLanguageSelectModal } from "../components/modals/GOGLanguageSelectModal";
import { pickStorageForInstall } from "../components/modals/PickStorageModal";
import type { Game, Result } from "../types/api";

/** Steam bridge shape — same minimal surface useGameActions
 *  consumes. */
interface SteamBridgeShape {
  runGame(appId: number): void;
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
  const { locations, defaultLocation, setCustomPath } = useStorageConfig();
  const [working, setWorking] = useState(false);

  const start = useCallback(
    async (game: Game): Promise<Result | null> => {
      setWorking(true);
      try {
        console.log("[useInstallFlow] opening storage picker for", game.title);
        const picked = await pickStorageForInstall(
          game.title,
          game.size_bytes,
          locations,
          defaultLocation,
          setCustomPath,
        );
        if (!picked) {
          console.log("[useInstallFlow] storage picker cancelled");
          return null;
        }
        const { storage, customPath } = picked;
        console.log(
          "[useInstallFlow] picked storage=%s customPath=%s",
          storage,
          customPath ?? "none",
        );

        if (game.store !== "gog") {
          console.log(
            "[useInstallFlow] installing %s/%s with storage=%s",
            game.store,
            game.store_game_id,
            storage,
          );
          return await actions.install(game.store, game.store_game_id, {
            storage,
            title: game.title,
          });
        }
        const langs = await getGogLangs(game.store_game_id).catch(() => null);
        const list = langs?.languages ?? [];
        if (list.length <= 1) {
          const language = list[0];
          return await actions.install(game.store, game.store_game_id, {
            language,
            storage,
            title: game.title,
          });
        }
        const language = await pickLanguageViaModal(game.title, list);
        if (!language) return null;
        return await actions.install(game.store, game.store_game_id, {
          language,
          storage,
          title: game.title,
        });
      } finally {
        setWorking(false);
      }
    },
    [actions, getGogLangs, locations, defaultLocation, setCustomPath],
  );

  return { isWorking: working || actions.isWorking, start };
}

/** Promise-wrapped showModal that resolves with the picked
 *  language (or null on cancel). Pre-selects the active UI
 *  language (which reflects the "auto" preference resolved to the
 *  system language) when the game offers it. */
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
        preferredTag={i18n.language}
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
