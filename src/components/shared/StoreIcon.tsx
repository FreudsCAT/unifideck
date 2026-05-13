/**
 * StoreIcon — brand badge for a store.
 *
 * Reads the visual config from `STORE_VISUALS` (Phase F1)
 * and renders an <img> with the canonical icon path. The
 * size is configurable so the same component works in a
 * 14px tag, a 24px row, or a 64px detail header.
 *
 * Falls back to a plain colored circle if the icon asset
 * is missing — ensures the layout doesn't break when an
 * asset path is wrong.
 */
import React, { FC, useState } from "react";
import { STORE_VISUALS } from "../../types/store";
import type { StoreId } from "../../types/api";

/** Props. */
interface Props {
  store: StoreId;
  size?: number;
}

/**
 * Square store glyph rendered next to game titles. Reads
 * the asset URL from STORE_VISUALS so theming changes
 * propagate from a single source of truth.
 */
export const StoreIcon: FC<Props> = ({ store, size = 16 }) => {
  const [errored, setErrored] = useState(false);
  const visual = STORE_VISUALS[store];
  if (!visual) return null;
  if (errored) {
    return (
      <span
        title={visual.display_name}
        style={{
          display: "inline-block",
          width: size, height: size,
          borderRadius: "50%",
          background: visual.brand_color,
        }}
      />
    );
  }
  return (
    <img
      src={visual.icon_path}
      alt={visual.display_name}
      width={size}
      height={size}
      onError={() => setErrored(true)}
      style={{ display: "inline-block", verticalAlign: "middle" }}
    />
  );
};
