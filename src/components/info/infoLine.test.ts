import { describe, expect, it } from "vitest";

import enUS from "../../i18n/locales/en-US.json";
import esES from "../../i18n/locales/es-ES.json";
import { buildInfoLine } from "./infoLine";

describe("buildInfoLine", () => {
  it("separates the genres the way the panel always has", () => {
    expect(buildInfoLine(["Action", "Indie"], "")).toBe("Action • Indie");
  });

  it("appends the Proton note after the genres", () => {
    expect(buildInfoLine(["Action"], "Runs on GE-Proton7-55")).toBe(
      "Action • Runs on GE-Proton7-55",
    );
  });

  it("does not open with a separator when a game has no genres", () => {
    // Store metadata is often thin for non-Steam titles.
    expect(buildInfoLine([], "Runs on GE-Proton7-55")).toBe("Runs on GE-Proton7-55");
  });

  it("does not end with a separator when no Proton was chosen", () => {
    expect(buildInfoLine(["Action", "Indie"], "")).not.toMatch(/•\s*$/);
  });

  it("is empty when there is nothing to say, so the row can be hidden", () => {
    expect(buildInfoLine([], "")).toBe("");
  });
});

describe("the Proton note's translations", () => {
  // The other locales are produced by DeepL at build time, and a
  // translator — human or machine — dropping the placeholder would leave
  // the user with a sentence that never names a Proton.
  it.each([
    ["en-US", enUS],
    ["es-ES", esES],
  ])("%s keeps the {{proton}} placeholder", (_tag, bundle) => {
    expect(bundle.gameInfoPanel.protonInUse).toContain("{{proton}}");
  });
});
