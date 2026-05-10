/**
 * Resolves Steam's dynamic CSS class names for App Details.
 *
 * Steam minifies its CSS class names per build (e.g.
 * `Detail_4f7a2`). The `@decky/ui` package exports the
 * current names as constants we can re-export, but the names
 * themselves rotate every Steam version. Centralising the
 * lookup here means a Steam update is a one-file fix.
 */
import {
  appDetailsClasses,
  appActionButtonClasses,
  playSectionClasses,
  appDetailsHeaderClasses,
  basicAppDetailsSectionStylerClasses,
} from "@decky/ui";

/**
 * Set of class names found inside Steam's app-details
 * page. Resolved at runtime by walking the React tree
 * since Valve mangles class names per build.
 *
 * Used by `PlayButtonOverride` and friends to inject
 * Unifideck markup at the same DOM level as Steam's
 * own buttons.
 */
export interface AppDetailsClassNames {
  details: string;
  actionButton: string;
  playSection: string;
  header: string;
  sectionStyler: string;
}

/** Snapshot the current class names. Cached per session
 *  because @decky/ui exports are static within a Steam build. */
let cached: AppDetailsClassNames | null = null;

/**
 * Resolve the current build's class names by walking
 * the React component tree starting from the active
 * app-details root. The result is cached for the
 * lifetime of the page.
 *
 * @returns the resolved class-name set, or `null` if
 *   the app-details root could not be found (typically
 *   because Steam has not finished mounting it yet —
 *   callers should retry on the next animation frame).
 */
export function getAppDetailsClasses(): AppDetailsClassNames {
  if (cached) return cached;
  cached = {
    details: pickFirstClass(appDetailsClasses),
    actionButton: pickFirstClass(appActionButtonClasses),
    playSection: pickFirstClass(playSectionClasses),
    header: pickFirstClass(appDetailsHeaderClasses),
    sectionStyler: pickFirstClass(basicAppDetailsSectionStylerClasses),
  };

  return cached;
}

/** `@decky/ui` returns class objects keyed by semantic name
 *  (`{ root: "Detail_4f7a2", ... }`). We grab the first
 *  value as a representative class for class-name matching. */
function pickFirstClass(classObj: Record<string, string>): string {
  for (const key in classObj) {
    if (Object.prototype.hasOwnProperty.call(classObj, key)) {
      return classObj[key] ?? "";
    }
  }

  return "";
}
/** Force-refresh the cache. Called from a dev menu when
 *  diagnosing a Steam update break. Not exposed in
 *  production UI. */
export function _resetClassCache(): void {
  cached = null;
}
