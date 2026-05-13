/**
 * AppDetailsPatch — patches Steam's App Details React tree.
 *
 * Steam renders `/library/app/<appId>` as a deep tree
 * containing a play section, header, basic details, etc.
 * We patch two locations :
 *
 *  1. The play section : wrapped with `<PlaySectionWrapper>`
 *     so we can override its rendering for Unifideck games.
 *  2. The basic details panel : append `<GameInfoPanel>`
 *     so users see metadata, scores and artwork.
 *
 * The patch is registered via SteamBridge's router patch ;
 * the returned handle is held by the plugin entry and
 * `.remove()`'d on plugin unload. Replaces the 400+ lines
 * of `patchGameDetailsRoute()` from the legacy index.tsx.
 */
import React from "react";
import {SteamBridge, type RouterPatchHandle} from "../lib/steam-bridge";
import { PlaySectionWrapper } from "../components/play";
import { GameInfoPanel } from "../components/info";

/** Node with props. */
interface NodeWithProps {
  props?: { children?: unknown; appid?: number; [k: string]: unknown };
  type?: { displayName?: string };
}

/** Stub component that's only used in the React DevTools
 *  display name. Helps debugging in production builds. */
export const AppDetailsPatch = (): null => null;

AppDetailsPatch.displayName = "Unifideck.AppDetailsPatch";

/** Apply the patch via the given bridge. Returns a handle
 *  whose `.remove()` undoes both injections. */
export function applyAppDetailsPatch(bridge: SteamBridge): RouterPatchHandle {
  return bridge.addRouterPatch("/library/app/:appid", (route: unknown) => {
    const r = route as NodeWithProps;
    const appIdStr = String(r.props?.appid ?? "");
    const appId = parseInt(appIdStr, 10);

    if (!Number.isFinite(appId)) return route;

    const playMatch = bridge.findInReactTree<NodeWithProps>(
      route,
      (node) => isPlaySection(node),
    );

    if (playMatch && playMatch.props) {
      const original = playMatch.props.children;
      playMatch.props.children = (
        <PlaySectionWrapper appId={appId}>
          {original as React.ReactNode}
        </PlaySectionWrapper>
      );
    }

    // Append game info panel under basic details
    const detailsMatch = bridge.findInReactTree<NodeWithProps>(
      route,
      (node) => isBasicDetails(node),
    );

    if (detailsMatch && detailsMatch.props) {
      const existing = detailsMatch.props.children;
      const merged = Array.isArray(existing) ? [...existing] : [existing];
      merged.push(<GameInfoPanel key="unifideck-info" appId={appId} />);
      detailsMatch.props.children = merged;
    }

    return route;
  });
}

/** Check whether play section. */
function isPlaySection(node: unknown): boolean {
  if (typeof node !== "object" || node === null) return false;

  const obj = node as { type?: unknown };
  const t = obj.type as { displayName?: string } | undefined;

  return t?.displayName?.includes("PlaySection") === true;
}

/** Check whether basic details. */
function isBasicDetails(node: unknown): boolean {
  if (typeof node !== "object" || node === null) return false;

  const obj = node as { type?: unknown };
  const t = obj.type as { displayName?: string } | undefined;

  return t?.displayName?.includes("BasicAppDetails") === true;
}
