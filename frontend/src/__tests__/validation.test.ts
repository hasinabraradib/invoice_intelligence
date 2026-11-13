import { describe, expect, it } from "vitest";
import { canApprove, checkDate, checkMoney, checkQuantity, checkUploadFile } from "@/lib/validation";
import { formatMoney } from "@/lib/format";
import { fieldAnchor } from "@/lib/fields";

describe("money", () => {
  it.each([
    ["1320.00", "1320.00"],
    ["1,320.5", "1320.5"],
    ["-45", "-45"],
    ["0.1", "0.1"],
  ])("accepts %s", (input, value) => {
    expect(checkMoney(input)).toEqual({ ok: true, value });
  });

  it.each(["1.234", "abc", "1e5", "12.", "1.2.3", "$5"])("rejects %s", (input) => {
    expect(checkMoney(input).ok).toBe(false);
  });

  it("treats empty as clearing the value", () => {
    expect(checkMoney("  ")).toEqual({ ok: true, value: null });
  });

  it("quantities allow 4 decimals", () => {
    expect(checkQuantity("2.5000").ok).toBe(true);
    expect(checkQuantity("2.50001").ok).toBe(false);
  });

  it("formats without floats", () => {
    expect(formatMoney("1234567.5", "BDT")).toBe("1,234,567.50 BDT");
    expect(formatMoney("-0.1", null)).toBe("-0.10");
    expect(formatMoney(null)).toBe("—");
  });
});

describe("dates", () => {
  it("accepts real ISO dates", () => {
    expect(checkDate("2026-03-15")).toEqual({ ok: true, value: "2026-03-15" });
    expect(checkDate("2024-02-29").ok).toBe(true); // leap year
  });

  it.each(["2026-02-30", "2025-02-29", "15/03/2026", "2026-3-5", "2026-13-01"])("rejects %s", (input) => {
    expect(checkDate(input).ok).toBe(false);
  });
});

describe("approve override rule", () => {
  it("needs a reviewer, and a reason only when errors remain", () => {
    expect(canApprove(0, "bo", "")).toBe(true);
    expect(canApprove(0, " ", "")).toBe(false);
    expect(canApprove(2, "bo", "")).toBe(false);
    expect(canApprove(2, "bo", "   ")).toBe(false);
    expect(canApprove(2, "bo", "Supplier confirmed")).toBe(true);
  });
});

describe("misc", () => {
  it("checks uploads before sending", () => {
    expect(checkUploadFile({ name: "a.pdf", size: 10 })).toBeNull();
    expect(checkUploadFile({ name: "a.exe", size: 10 })).toMatch(/Unsupported/);
    expect(checkUploadFile({ name: "a.pdf", size: 0 })).toMatch(/empty/);
    expect(checkUploadFile({ name: "a.pdf", size: 21 * 1024 * 1024 })).toMatch(/Larger/);
  });

  it("builds anchors for issue links", () => {
    expect(fieldAnchor("total")).toBe("field-total");
    expect(fieldAnchor("line_items[2].line_total")).toBe("field-line-2-line_total");
    expect(fieldAnchor(null)).toBe("fields");
  });
});
