/**
 * AppDetailsPatch — patches Steam's App Details React tree.
 *
 * Steam renders `/library/app/<appId>` via a route whose
 * `renderFunc` produces the actual app-details tree. We
 * intercept that renderFunc with `afterPatch` and inject two
 * Unifideck components into Steam's `InnerContainer` :
 *
 *   1. `<PlaySectionWrapper>` — replaces / decorates the
 *       native Play / Install row for non-Steam shortcuts.
 *   2. `<GameInfoPanel>`    — metadata + scores panel
 *       rendered right after the PlaySection wrapper.
 *
 * The patch is registered via SteamBridge's router patch ;
 * the returned handle is held by the plugin entry and
 * `.remove()`'d on plugin unload.
 *
 * Notes on robustness :
 *  - Anchors via CSS class names (`appDetailsClasses?.InnerContainer`,
 *    `playSectionClasses?.Container`), NOT React `displayName`
 *    which Steam mangles in production builds.
 *  - `__unifideckPatched` marker on `renderFunc` prevents
 *    double-patching when ProtonDB/HLTB co-patch the same
 *    route (they use their own marker, `__deckyPatch`).
 *  - Position-correction : if our injected element drifts
 *    past index 3 after a Steam restart, splice it out and
 *    re-insert at the anchored index.
 *  - Non-Steam-game gate (`appId > 2_000_000_000`) so we
 *    only override shortcuts, never first-party Steam games.
 */
import {
  afterPatch,
  createReactTreePatcher,
  appDetailsClasses,
  appDetailsHeaderClasses,
  playSectionClasses,
} from "@decky/ui";
import { SteamBridge, type RouterPatchHandle } from "../lib/steam-bridge";
import { findInReactTree } from "../lib/steam-bridge/react-tree";
import { getGameStateVersion } from "../lib/game-state-version";
import { InjectedSubtreeProvider } from "../contexts/InjectedSubtreeProvider";
import { PlaySectionWrapper } from "../components/play";
import { GameInfoPanel } from "../components/info";

/** Stub component that's only used in the React DevTools
 *  display name. Helps debugging in production builds. */
export const AppDetailsPatch = (): null => null;
AppDetailsPatch.displayName = "Unifideck.AppDetailsPatch";

/** Apply the patch via the given bridge. Returns a handle
 *  whose `.remove()` undoes both injections. */
export function applyAppDetailsPatch(bridge: SteamBridge): RouterPatchHandle {
  return bridge.addRouterPatch("/library/app/:appid", (routerTree: unknown) => {
    const routeProps = findInReactTree<RouteProps>(
      routerTree,
      (x) => (x as RouteProps | null)?.renderFunc != null,
    );
    if (!routeProps) return routerTree;

    // Skip if we've already wrapped this renderFunc. The marker is
    // Unifideck-specific so it doesn't collide with ProtonDB / HLTB.
    if ((routeProps.renderFunc as PatchedFn).__unifideckPatched) {
      return routerTree;
    }

    const patchHandler = createReactTreePatcher(
      [
        (tree: unknown) =>
          findInReactTree<NodeWithChildren>(
            tree,
            (x) =>
              (x as NodeWithOverview | null)?.props?.children?.props?.overview != null,
          )?.props?.children as unknown,
      ],
      (_args: unknown[], ret: unknown) => {
        try {
          injectIntoTree(ret);
        } catch (e) {
          console.error("[Unifideck] AppDetailsPatch handler error:", e);
        }
        return ret;
      },
    );

    afterPatch(routeProps, "renderFunc", patchHandler);
    (routeProps.renderFunc as PatchedFn).__unifideckPatched = true;
    return routerTree;
  });
}

/** Walk the rendered route tree and splice our components
 *  into the `InnerContainer` for non-Steam shortcuts. */
function injectIntoTree(ret: unknown): void {
  const overviewNode = findInReactTree<NodeWithOverview>(
    ret,
    (x) => (x as NodeWithOverview | null)?.props?.children?.props?.overview != null,
  );
  const overview = overviewNode?.props?.children?.props?.overview;
  if (!overview) return;
  const appId = overview.appid;

  // Only override non-Steam shortcuts (appId > 2 billion).
  if (!(appId > 2_000_000_000)) return;

  const innerContainer = findInReactTree<NodeWithChildren>(
    ret,
    (x) => {
      const n = x as NodeWithChildren | null;
      return (
        Array.isArray(n?.props?.children) &&
        typeof n?.props?.className === "string" &&
        appDetailsClasses?.InnerContainer != null &&
        n.props.className.includes(appDetailsClasses.InnerContainer)
      );
    },
  );

  // If the container isn't ready yet, skip — the patcher fires
  // again on the next render.
  if (
    !innerContainer ||
    !innerContainer.props ||
    !Array.isArray(innerContainer.props.children)
  ) return;

  const children = innerContainer.props!.children as unknown[];
  const playWrapperKey = `unifideck-play-wrapper-${appId}`;
  const gameInfoKey = `unifideck-game-info-${appId}`;
  const version = getGameStateVersion(appId);

  injectPlayWrapper(children, appId, playWrapperKey, version);
  injectGameInfoPanel(children, appId, playWrapperKey, gameInfoKey, version);
}

function injectPlayWrapper(
  children: unknown[],
  appId: number,
  baseKey: string,
  version: number,
): void {
  const existingIdx = children.findIndex(
    (c) => keyOf(c).startsWith(baseKey),
  );

  if (existingIdx === -1) {
    const idx = findPlaySectionInsertIndex(children);
    // Wrap in InjectedSubtreeProvider so the contexts the
    // children read (DownloadContext, AuthContext, ...) are
    // available — Steam's React tree is rendered outside our
    // top-level RootProvider so we have to provide a fresh
    // context stack here.
    children.splice(
      idx,
      0,
      <InjectedSubtreeProvider key={`${baseKey}-v${version}`}>
        <PlaySectionWrapper appId={appId}>{null}</PlaySectionWrapper>
      </InjectedSubtreeProvider>,
    );
    return;
  }

  // Position-correction : reposition if drifted past index 3.
  if (existingIdx > 3) {
    const [el] = children.splice(existingIdx, 1);
    const correctIdx = findPlaySectionInsertIndex(children);
    children.splice(correctIdx, 0, el);
  }
}

function injectGameInfoPanel(
  children: unknown[],
  appId: number,
  wrapperKey: string,
  baseKey: string,
  version: number,
): void {
  const existingIdx = children.findIndex(
    (c) => keyOf(c).startsWith(baseKey),
  );

  if (existingIdx === -1) {
    const wrapperIdx = children.findIndex(
      (c) => keyOf(c).startsWith(wrapperKey),
    );
    const idx =
      wrapperIdx >= 0
        ? wrapperIdx + 1
        : findPlaySectionInsertIndex(children) + 1;
    // Same wrapping rationale as `injectPlayWrapper`. GameInfoPanel
    // itself uses module-level caches today but its sub-sections
    // call `useRPC` / `useTranslation` which work fine standalone ;
    // we still wrap for symmetry + to future-proof against any
    // sub-section growing a context dependency.
    children.splice(
      idx,
      0,
      <InjectedSubtreeProvider key={`${baseKey}-v${version}`}>
        <GameInfoPanel appId={appId} />
      </InjectedSubtreeProvider>,
    );
    return;
  }

  // Position-correction : keep panel immediately after wrapper.
  const wrapperIdx = children.findIndex(
    (c) => keyOf(c).startsWith(wrapperKey),
  );
  if (wrapperIdx >= 0 && existingIdx !== wrapperIdx + 1) {
    const [el] = children.splice(existingIdx, 1);
    const newWrapperIdx = children.findIndex(
      (c) => keyOf(c).startsWith(wrapperKey),
    );
    children.splice(newWrapperIdx + 1, 0, el);
  }
}

/** Resolve where PlaySectionWrapper should land in the
 *  `InnerContainer`'s children array. Tries (in order) :
 *    1. Right after the native PlaySection container.
 *    2. Right after the AppDetails header / TopCapsule.
 *    3. After the first non-Unifideck child (header is index 0).
 *    4. Fallback: index 1.
 */
function findPlaySectionInsertIndex(children: unknown[]): number {
  if (playSectionClasses?.Container) {
    const idx = children.findIndex((c) =>
      classOf(c).includes(playSectionClasses.Container),
    );
    if (idx >= 0) return idx + 1;
  }

  const headerIdx = children.findIndex((c) => {
    const cn = classOf(c);
    return (
      (appDetailsHeaderClasses?.TopCapsule &&
        cn.includes(appDetailsHeaderClasses.TopCapsule)) ||
      (appDetailsClasses?.Header && cn.includes(appDetailsClasses.Header))
    );
  });
  if (headerIdx >= 0) return headerIdx + 1;

  for (let i = 0; i < children.length; i++) {
    if (keyOf(children[i]).startsWith("unifideck-")) continue;
    return i + 1;
  }

  return Math.min(1, children.length);
}

function keyOf(node: unknown): string {
  return (node as { key?: string } | null)?.key ?? "";
}

function classOf(node: unknown): string {
  const cn = (node as NodeWithChildren | null)?.props?.className;
  return typeof cn === "string" ? cn : "";
}

interface RouteProps {
  renderFunc: (...args: unknown[]) => unknown;
}

type PatchedFn = ((...args: unknown[]) => unknown) & {
  __unifideckPatched?: boolean;
};

interface NodeWithChildren {
  props?: {
    children?: unknown;
    className?: unknown;
    [k: string]: unknown;
  };
}

interface NodeWithOverview {
  props?: {
    children?: {
      props?: { overview?: { appid: number } };
    };
  };
}
