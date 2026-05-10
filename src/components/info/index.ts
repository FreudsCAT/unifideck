/**
 * Info panel — barrel export.
 *
 * Decomposes the legacy GameInfoPanel.tsx (1232 LOC) into
 * a container + three focused sections (Header, Metadata,
 * Scores). The container reads `useGameInfo` and forwards
 * the data to each section as props. Each section is a pure
 * presentational component — it receives data, renders
 * markup, that's it.
 */
export { GameInfoPanel } from "./GameInfoPanel";
export { GameInfoHeader } from "./GameInfoHeader";
export { GameInfoMetadata } from "./GameInfoMetadata";
export { GameInfoScores } from "./GameInfoScores";
