/**
 * Tests for gog-language-match: normalizing the inconsistent
 * language labels gogdl emits across titles, and matching a raw
 * option list against a target locale tag.
 */
import { describe, it, expect } from "vitest";
import { normalizeGogLanguage, matchGogLanguage } from "./gog-language-match";

describe("normalizeGogLanguage", () => {
  it("maps full English names", () => {
    expect(normalizeGogLanguage("English")).toBe("en");
    expect(normalizeGogLanguage("Spanish")).toBe("es");
    expect(normalizeGogLanguage("german")).toBe("de");
  });

  it("maps native names", () => {
    expect(normalizeGogLanguage("Deutsch")).toBe("de");
    expect(normalizeGogLanguage("Français")).toBe("fr");
    expect(normalizeGogLanguage("Español")).toBe("es");
    expect(normalizeGogLanguage("日本語")).toBe("ja");
  });

  it("maps 2-letter ISO codes and BCP-47 tags", () => {
    expect(normalizeGogLanguage("en")).toBe("en");
    expect(normalizeGogLanguage("en-US")).toBe("en");
    expect(normalizeGogLanguage("pt-BR")).toBe("pt");
    expect(normalizeGogLanguage("zh-Hans")).toBe("zh");
    expect(normalizeGogLanguage("fr_FR")).toBe("fr");
  });

  it("maps 3-letter codes and GOG legacy quirks", () => {
    expect(normalizeGogLanguage("eng")).toBe("en");
    expect(normalizeGogLanguage("esp")).toBe("es"); // GOG legacy
    expect(normalizeGogLanguage("spa")).toBe("es");
    expect(normalizeGogLanguage("fre")).toBe("fr");
    expect(normalizeGogLanguage("br")).toBe("pt"); // Brazilian
    expect(normalizeGogLanguage("cn")).toBe("zh");
  });

  it("unpacks composite 'Name (code)' forms", () => {
    expect(normalizeGogLanguage("English (en)")).toBe("en");
    expect(normalizeGogLanguage("Spanish (esp)")).toBe("es");
    expect(normalizeGogLanguage("en (English)")).toBe("en");
    expect(normalizeGogLanguage("Português (Brasil)")).toBe("pt");
  });

  it("returns null for unrecognised labels", () => {
    expect(normalizeGogLanguage("Klingon")).toBeNull();
    expect(normalizeGogLanguage("")).toBeNull();
    expect(normalizeGogLanguage("auto")).toBeNull();
  });
});

describe("matchGogLanguage", () => {
  it("picks the raw option matching the target base language", () => {
    expect(matchGogLanguage(["English", "esp", "Deutsch"], "es-ES")).toBe("esp");
    expect(matchGogLanguage(["en", "fr", "de"], "fr-FR")).toBe("fr");
    expect(matchGogLanguage(["English (en)", "Spanish (esp)"], "en-US")).toBe("English (en)");
  });

  it("prefers an exact tag match over a base match", () => {
    expect(matchGogLanguage(["zh-Hans", "zh-TW"], "zh-TW")).toBe("zh-TW");
  });

  it("returns the verbatim raw string for passing to gogdl", () => {
    expect(matchGogLanguage(["en-US", "pt-BR"], "pt-BR")).toBe("pt-BR");
  });

  it("returns null when the game offers no equivalent", () => {
    expect(matchGogLanguage(["English", "French"], "ja-JP")).toBeNull();
    expect(matchGogLanguage([], "en-US")).toBeNull();
    expect(matchGogLanguage(["English"], "auto")).toBeNull();
  });
});
