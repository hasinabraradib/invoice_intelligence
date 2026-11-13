"use client";
import type { InvoiceResult } from "@/lib/api";

type Props = { invoices: InvoiceResult[]; selected: number; onSelect: (segmentIndex: number) => void };

function pages(invoice: InvoiceResult): string {
  if (invoice.page_start == null) return "";
  return invoice.page_start === invoice.page_end ? `page ${invoice.page_start}` : `pages ${invoice.page_start}–${invoice.page_end}`;
}

/** For batch documents: pick which invoice to review. Hidden when there is only one. */
export function InvoiceSwitcher({ invoices, selected, onSelect }: Props) {
  if (invoices.length < 2) return null;
  const index = invoices.findIndex((i) => i.segment_index === selected);
  const reviewCount = invoices.filter((i) => i.needs_review).length;
  return (
    <div className="card flex flex-wrap items-end gap-2">
      <div className="min-w-0 flex-1">
        <label htmlFor="invoice-select" className="label">
          Invoice in this document ({invoices.length} found{reviewCount ? `, ${reviewCount} need review` : ""})
        </label>
        <select id="invoice-select" className="input" value={selected} onChange={(e) => onSelect(Number(e.target.value))}>
          {invoices.map((invoice, i) => (
            <option key={invoice.invoice_id} value={invoice.segment_index}>
              {`${i + 1} of ${invoices.length} · ${pages(invoice)} · ${String(invoice.fields.invoice_number?.value ?? "no number")}${invoice.needs_review ? " · needs review" : ""}${invoice.extracted ? "" : " · not extracted yet"}`}
            </option>
          ))}
        </select>
      </div>
      <div className="flex gap-2">
        <button type="button" className="btn-secondary" disabled={index <= 0}
          onClick={() => onSelect(invoices[index - 1].segment_index)}>Previous invoice</button>
        <button type="button" className="btn-secondary" disabled={index >= invoices.length - 1}
          onClick={() => onSelect(invoices[index + 1].segment_index)}>Next invoice</button>
      </div>
    </div>
  );
}
