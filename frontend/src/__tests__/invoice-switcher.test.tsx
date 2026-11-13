import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { InvoiceResult } from "@/lib/api";
import { InvoiceSwitcher } from "@/components/review/InvoiceSwitcher";

function invoice(segment: number, start: number, end: number, number: string, needsReview = false): InvoiceResult {
  return {
    invoice_id: `id-${segment}`, document_id: "doc", segment_index: segment, page_start: start, page_end: end,
    extracted: true, needs_review: needsReview, overall_confidence: 0.9, extracted_at: "2026-01-01T00:00:00Z",
    fields: { invoice_number: { value: number, status: "found", confidence: 0.95, reasons: [] } },
    line_items: [], issues: [], duplicates: [],
  } as InvoiceResult;
}

describe("invoice switcher", () => {
  it("is hidden for a single invoice", () => {
    const { container } = render(<InvoiceSwitcher invoices={[invoice(0, 1, 1, "A")]} selected={0} onSelect={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists invoices with pages and moves between them", async () => {
    const onSelect = vi.fn();
    const invoices = [invoice(0, 1, 1, "A-1"), invoice(1, 2, 3, "B-2", true), invoice(2, 4, 4, "C-3")];
    render(<InvoiceSwitcher invoices={invoices} selected={1} onSelect={onSelect} />);

    expect(screen.getByLabelText(/3 found, 1 need review/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /2 of 3 · pages 2–3 · B-2 · needs review/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next invoice" }));
    expect(onSelect).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByRole("button", { name: "Previous invoice" }));
    expect(onSelect).toHaveBeenLastCalledWith(0);
    await userEvent.selectOptions(screen.getByLabelText(/Invoice in this document/), "2");
    expect(onSelect).toHaveBeenLastCalledWith(2);
  });
});
