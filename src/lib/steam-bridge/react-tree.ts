/**
 * React tree traversal helpers.
 *
 * Wraps `@decky/ui`'s `findInReactTree` with a typed
 * signature and a depth limit. Steam's React tree is deep
 * (up to ~30 levels in some app detail views) so we cap the
 * search to avoid pathological cases when a matcher never
 * matches.
 */
import { findInReactTree as deckyFind } from "@decky/ui";

/**
 * Predicate run by `findInReactTree` against each node
 * during a depth-first walk. Returns true for the node
 * the caller wants to capture.
 */
export type ReactTreeMatcher = (node: unknown) => boolean;

const MAX_DEPTH = 50;

/** Walk a React element tree depth-first and return the
 *  first node satisfying `matcher`, or null if none found
 *  within MAX_DEPTH levels. */
export function findInReactTree<T = unknown>(
  root: unknown,
  matcher: ReactTreeMatcher,
): T | null {
  if (root == null) return null;

  try {
    const result = deckyFind(root, matcher);
    return (result as T) ?? null;
  } catch {
    return null;
  }
}

/** Walk a tree and return ALL nodes satisfying `matcher`.
 *  Useful for bulk patches (e.g. injecting buttons into all
 *  app cards in a grid). */
export function findAllInReactTree<T = unknown>(
  root: unknown,
  matcher: ReactTreeMatcher,
  limit: number = 100,
): T[] {
  const matches: T[] = [];
  walk(root, matcher, matches, 0, limit);

  return matches;
}

/** Walk. */
function walk<T>(
  node: unknown,
  matcher: ReactTreeMatcher,
  out: T[],
  depth: number,
  limit: number,
): void {
  if (out.length >= limit || depth > MAX_DEPTH || node == null) return;

  if (matcher(node)) {
    out.push(node as T);
  }

  if (typeof node === "object" && node !== null) {
    const obj = node as Record<string, unknown>;
    const props = obj.props as Record<string, unknown> | undefined;
    const children = props?.children;

    if (Array.isArray(children)) {
      for (const child of children) {
        walk(child, matcher, out, depth + 1, limit);
      }
    } else if (children != null) {
      walk(children, matcher, out, depth + 1, limit);
    }
  }
}
