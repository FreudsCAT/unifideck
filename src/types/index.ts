/**
 * Types subpackage — barrel export.
 *
 * Single import surface for every DTO and enum used across
 * the frontend. Mirrors the backend `core/types/` package so
 * Python and TypeScript stay in lockstep.
 *
 * Anti-pattern explicitly avoided: type duplicates with
 * subtly different field sets (e.g. legacy `Game` vs
 * backend `Game`). Every consumer imports from this barrel,
 * never directly from a sibling file inside this package.
 */
export * from "./api";
export * from "./events";
export * from "./store";
export * from "./steam";
export * from "./downloads";
export * from "./playtime";
export * from "./syncProgress";
