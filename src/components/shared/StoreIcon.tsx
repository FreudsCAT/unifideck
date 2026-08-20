/**
 * StoreIcon — brand badge for a store.
 *
 * Vector-icon based (react-icons) — staging used the same set so the
 * visual matches across the codebase. Falls back to a generic
 * `FaGamepad` glyph for unknown stores so the layout never breaks.
 */
import { FC } from "react";
import {
  SiAmazongames,
  SiEpicgames,
  SiGogdotcom,
  SiBattledotnet,
  SiUbisoft,
} from "react-icons/si";
import { FaGamepad, FaSteam, FaXbox } from "react-icons/fa";
import type { IconType } from "react-icons";
import type { StoreId } from "../../types/api";

const STORE_ICONS: Record<StoreId, IconType> = {
  steam: FaSteam,
  epic: SiEpicgames,
  gog: SiGogdotcom,
  amazon: SiAmazongames,
  microsoft: FaXbox,
  ubisoft: SiUbisoft,
  battlenet: SiBattledotnet,
};

interface Props {
  store: StoreId;
  size?: number | string;
  color?: string;
}

export const StoreIcon: FC<Props> = ({
  store,
  size = 16,
  color = "inherit",
}) => {
  const Icon = STORE_ICONS[store] ?? FaGamepad;
  return <Icon size={size} color={color} />;
};
