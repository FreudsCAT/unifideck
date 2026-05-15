/**
 * PlaySectionWrapper — dispatcher for the Play section.
 *
 * Reads `usePlaySection(appId)` and renders one of three
 * sub-components based on the discriminated state. When the
 * state is "steam-native", the wrapper passes through to
 * Steam's own play section without modification — that's the
 * default behaviour for non-Unifideck games.
 *
 * The wrapper itself is pure presentation : the decision
 * logic is in `usePlaySection`, the CDP hide/show is in
 * `useHidePlaySection`, the actions are in `useGameActions`.
 * This component just glues them.
 */
import React, { FC, ReactNode, useEffect } from "react";
import { usePlaySection } from "../../hooks/usePlaySection";
import { useHidePlaySection } from "../../hooks/useHidePlaySection";
import { NotInstalledButtons } from "./NotInstalledButtons";
import { DownloadingButtons } from "./DownloadingButtons";
import { InstalledButtons } from "./InstalledButtons";
import { injectPlayFocusStyles } from "./play.css";

/**
 * Props of {@link PlaySectionWrapper}. The `appId` is
 * the Steam shortcut id ; everything else is resolved
 * through hooks inside the wrapper.
 */
export interface PlaySectionWrapperProps {
  appId: number;
  children: ReactNode;  // Steam's native play section
}

/**
 * Top-level wrapper rendered in place of Steam's own
 * Play section for Unifideck games. Picks the right
 * variant (Not-installed / Downloading / Installed)
 * based on `usePlaySection` and forwards the action
 * callbacks coming from `useGameActions`.
 */
export const PlaySectionWrapper: FC<PlaySectionWrapperProps> = ({appId, children}) => {
  const state = usePlaySection(appId);
  // Hide Steam's native section if we overriding it
  useHidePlaySection(appId, state.shouldOverride);
  useEffect(() => { injectPlayFocusStyles(); }, []);
  if (!state.shouldOverride) {
    return <>{children}</>;
  }
  switch (state.kind) {
    case "not-installed":
      return <NotInstalledButtons appId={appId} />;
    case "downloading":
      return <DownloadingButtons download={state.download} />;
    case "installed":
      return <InstalledButtons appId={state.appId} />;
    default:
      // Exhaustiveness — TS will flag missing branches
      return <>{children}</>;
  }
};
