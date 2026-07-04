import { FC, ReactNode } from "react";
import { ConfirmModal } from "@decky/ui";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

interface Props {
  version: string;
  body: string;
  closeModal?: () => void;
}

const parseInline = (text: string) => {
  const boldParts = text.split("**");
  const result: ReactNode[] = [];

  boldParts.forEach((part, boldIdx) => {
    const isBold = boldIdx % 2 !== 0;

    // Split by backticks for inline code styling
    const codeParts = part.split("`");
    const parsedCodeParts = codeParts.map((subPart, codeIdx) => {
      const isCode = codeIdx % 2 !== 0;
      if (isCode) {
        return (
          <code
            key={`${boldIdx}-${codeIdx}`}
            style={{
              background: "rgba(255, 255, 255, 0.12)",
              padding: "2px 6px",
              borderRadius: "4px",
              fontFamily: "monospace",
              fontSize: "0.9em",
              color: "#f59e0b",
            }}
          >
            {subPart}
          </code>
        );
      }
      return subPart;
    });

    if (isBold) {
      result.push(
        <strong
          key={boldIdx}
          style={{
            color: "#38bdf8",
            fontWeight: "bold",
          }}
        >
          {parsedCodeParts}
        </strong>,
      );
    } else {
      result.push(...parsedCodeParts);
    }
  });

  return result;
};

const parseMarkdown = (text: string, t: TFunction) => {
  if (!text) {
    return (
      <div style={{ opacity: 0.6, fontStyle: "italic", padding: "10px 0" }}>
        {t("updater.noReleaseNotes", {
          defaultValue: "No release notes available.",
        })}
      </div>
    );
  }

  const lines = text.split("\n");
  return lines.map((line, idx) => {
    const trimmed = line.trim();

    // Headers
    if (trimmed.startsWith("### ")) {
      return (
        <h3
          key={idx}
          style={{
            margin: "12px 0 6px 0",
            fontSize: "14px",
            fontWeight: "bold",
            color: "#ffffff",
          }}
        >
          {parseInline(trimmed.slice(4))}
        </h3>
      );
    }
    if (trimmed.startsWith("## ")) {
      return (
        <h2
          key={idx}
          style={{
            margin: "16px 0 8px 0",
            fontSize: "16px",
            fontWeight: "bold",
            color: "#ffffff",
            borderBottom: "1px solid rgba(255,255,255,0.1)",
            paddingBottom: "4px",
          }}
        >
          {parseInline(trimmed.slice(3))}
        </h2>
      );
    }
    if (trimmed.startsWith("# ")) {
      return (
        <h1
          key={idx}
          style={{
            margin: "18px 0 10px 0",
            fontSize: "18px",
            fontWeight: "bold",
            color: "#ffffff",
            borderBottom: "1px solid rgba(255,255,255,0.15)",
            paddingBottom: "6px",
          }}
        >
          {parseInline(trimmed.slice(2))}
        </h1>
      );
    }

    // Lists
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      return (
        <li
          key={idx}
          style={{
            marginInlineStart: "20px",
            marginBottom: "6px",
            listStyleType: "disc",
            lineHeight: "1.4",
          }}
        >
          {parseInline(trimmed.slice(2))}
        </li>
      );
    }

    // Empty space/paragraph division
    if (trimmed === "") {
      return <div key={idx} style={{ height: "6px" }} />;
    }

    // Paragraph
    return (
      <p
        key={idx}
        style={{
          margin: "4px 0",
          lineHeight: "1.4",
        }}
      >
        {parseInline(line)}
      </p>
    );
  });
};

export const ReleaseNotesModal: FC<Props> = ({ version, body, closeModal }) => {
  const { t } = useTranslation();

  return (
    <ConfirmModal
      strTitle={t("updater.modalTitle", {
        version,
        defaultValue: `UNIFIDECK v${version} — Release Notes`,
      })}
      strOKButtonText={t("common.ok", { defaultValue: "OK" })}
      onOK={closeModal}
      onCancel={closeModal}
      bHideCloseIcon={false}
    >
      <div
        style={{
          maxHeight: "320px",
          overflowY: "auto",
          padding: "12px 16px",
          background: "rgba(0, 0, 0, 0.25)",
          borderRadius: "6px",
          marginTop: "12px",
          fontSize: "13px",
          color: "#e5e7eb",
          fontFamily:
            'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        }}
      >
        {parseMarkdown(body, t)}
      </div>
    </ConfirmModal>
  );
};
