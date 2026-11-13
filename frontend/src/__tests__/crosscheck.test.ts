import { describe, expect, it } from "vitest";
import type { FindingView } from "@/lib/api";
import { evidenceLinks, kindLabel } from "@/lib/crosscheck";

const check = { document_a: { id: "pdf", filename: "batch.pdf" }, document_b: { id: "led", filename: "ledger.xlsx" } };

function finding(overrides: Partial<FindingView>): FindingView {
  return {
    kind: "amount_mismatch", severity: "error", field: "total", value_a: "20.00", value_b: "20.02",
    message: "", invoice_a_id: "inv-a", invoice_b_id: null, segment_a: 1, segment_b: null,
    page_a: 3, page_b: null, sheet_b: "Purchases", row_b: 5, ...overrides,
  };
}

describe("cross-check evidence links", () => {
  it("links the invoice page on side A and the ledger row on side B", () => {
    const [a, b] = evidenceLinks(check, finding({}));
    expect(a).toEqual({ label: "invoice 2, page 3", href: "/documents/pdf?invoice=1&page=3" });
    expect(b).toEqual({ label: "Purchases, row 5", href: "/documents/led?sheet=Purchases&row=5" });
  });

  it("links invoice pages on both sides for PDF vs PDF", () => {
    const [, b] = evidenceLinks(check, finding({ sheet_b: null, row_b: null, invoice_b_id: "inv-b", segment_b: 0, page_b: 1 }));
    expect(b?.href).toBe("/documents/led?invoice=0&page=1");
  });

  it("has no link for a side that has nothing (missing in PDF)", () => {
    const [a, b] = evidenceLinks(check, finding({ kind: "missing_in_pdf", invoice_a_id: null, segment_a: null, page_a: null }));
    expect(a).toBeNull();
    expect(b?.label).toBe("Purchases, row 5");
  });

  it("labels kinds in words", () => {
    expect(kindLabel("missing_in_ledger")).toBe("Not in ledger");
    expect(kindLabel("something_new")).toBe("something new");
  });
});
