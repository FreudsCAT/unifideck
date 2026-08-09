/**
 * GameInfoSynopsisSection — collapsible game description block.
 *
 * Plain-text rendering of {@link GameMetadata.description}. The
 * parent panel owns the open/closed flag (toggled by the Synopsis
 * button in {@link GameInfoCompatRow}); when closed, this
 * component is not rendered at all.
 */
import { FC } from "react";
import { Focusable } from "@decky/ui";

interface Props {
  description: string;
}

export const GameInfoSynopsisSection: FC<Props> = ({ description }) => {
  return (
    <Focusable
      flow-children="column"
      onActivate={() => {}}
      noFocusRing
      className="unifideck-synopsis-section"
      style={{
        background: "rgba(0, 0, 0, 0.3)",
        borderRadius: 6,
        padding: "12px 16px",
        fontSize: 14,
        color: "#acb2b8",
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
      }}
      onFocus={(e: React.FocusEvent<HTMLDivElement>) => {
        e.currentTarget.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }}
    >
      {description}
    </Focusable>
  );
};
