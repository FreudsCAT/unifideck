/**
 * Minimal stand-in for React under vitest. At runtime React is
 * peer-provided by the Steam webview (SP_REACT) and is deliberately
 * not installed in node_modules, so tests that import steam-bridge
 * modules resolve "react" to this stub via the vitest alias.
 */
const React = {
  createElement: (
    type: unknown,
    props: unknown,
    ...children: unknown[]
  ): Record<string, unknown> => ({ type, props, children }),
};

export default React;
