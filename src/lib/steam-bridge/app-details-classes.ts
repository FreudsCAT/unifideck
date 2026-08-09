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
  findClassModule,
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

/**
 * The specific Steam class names that CSS Loader themes hook.
 *
 * Themes are authored against Steam's readable CSS-module names
 * (`appdetailsplaysection_MenuButton_3qDWQ`) and CSS Loader expands
 * each one, via its `css_translations.json`, into every historical
 * alias plus the current minified class. So a theme rule reaches any
 * element wearing the *live* class — including ours, if we put it
 * there. Wearing these is what makes our play row themeable at all;
 * without them no theme selector can ever match our buttons.
 *
 * Two gotchas, both verified live against the "Round" theme:
 *  - The icon-button rule is a DESCENDANT selector
 *    (`.AppButtons .MenuButton`), so `appButtons` must be on an
 *    ancestor or the button rule silently does not apply.
 *  - The primary-button rule is a COMPOUND selector
 *    (`.AppActionButton.PlayButtonContainer`) — both names have to
 *    land on the same element.
 */
export interface ThemeableClassNames {
  /** Required ancestor for the icon-button rule to match. */
  appButtons: string;
  /** Steam's square 48x48 icon button. */
  menuButton: string;
  /** Controller-config variant of `menuButton` (tighter padding). */
  controllerConfigButton: string;
  /** Primary action button, half of a compound selector. */
  actionButton: string;
  /** Primary action button, other half of the compound selector. */
  playButtonContainer: string;
}

let themeable: ThemeableClassNames | null = null;

/**
 * Resolve the themeable class names for the current Steam build.
 *
 * Prefers the `@decky/ui` static exports; if one of them drifts (they
 * are snapshots of a Steam build and do rotate), falls back to finding
 * the class module by SHAPE rather than by position or hardcoded
 * string. A name we cannot resolve degrades to `""`, which just means
 * that one button stops being themeable — never a crash or a missing
 * button.
 */
export function getThemeableClasses(): ThemeableClassNames {
  if (themeable) return themeable;

  let play = asClassMap(playSectionClasses);
  let styler = asClassMap(basicAppDetailsSectionStylerClasses);
  let action = asClassMap(appActionButtonClasses);

  if (!play.MenuButton) {
    play = asClassMap(
      findClassModule((m) => !!m.MenuButton && !!m.ControllerConfigButton),
    );
  }
  if (!styler.AppButtons) {
    styler = asClassMap(
      findClassModule((m) => !!m.AppButtons && !!m.AppActionButton),
    );
  }
  if (!action.PlayButtonContainer) {
    action = asClassMap(findClassModule((m) => !!m.PlayButtonContainer));
  }

  themeable = {
    appButtons: styler.AppButtons ?? "",
    menuButton: play.MenuButton ?? "",
    controllerConfigButton: play.ControllerConfigButton ?? "",
    actionButton: styler.AppActionButton ?? "",
    playButtonContainer: action.PlayButtonContainer ?? "",
  };

  return themeable;
}

/** Narrow an unknown `@decky/ui` class export to a lookup map. */
function asClassMap(obj: unknown): Record<string, string | undefined> {
  return (obj ?? {}) as Record<string, string | undefined>;
}

/** Force-refresh the cache. Called from a dev menu when
 *  diagnosing a Steam update break. Not exposed in
 *  production UI. */
export function _resetClassCache(): void {
  cached = null;
  themeable = null;
}
