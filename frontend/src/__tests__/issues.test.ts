import { describe, expect, it } from "vitest";
import { issueLabel } from "@/lib/issues";

describe("issueLabel", () => {
  it("names known codes in plain language", () => {
    expect(issueLabel("TOTAL_MISMATCH")).toBe("Total doesn't add up");
    expect(issueLabel("OCR_VISION_DISAGREE")).toBe("OCR and image disagree");
  });
  it("falls back to a readable version of unknown codes, and says when there is none", () => {
    expect(issueLabel("SOMETHING_NEW")).toBe("Something new");
    expect(issueLabel(null)).toBe("No issue recorded");
  });
});
