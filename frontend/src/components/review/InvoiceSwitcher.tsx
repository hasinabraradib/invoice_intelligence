"use client";
import type { InvoiceResult } from "@/lib/api";
import { ChevronLeft, ChevronRight, Layers } from "lucide-react";

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
    <div className="card space-y-3 border-accent-line bg-accent-soft/40">
      <div className="flex items-center gap-3">
        <span aria-hidden="true" className="grid size-8 shrink-0 place-items-center rounded-xl bg-surface text-accent-ink shadow-soft">
          <Layers className="size-4" strokeWidth={1.75} />
        </span>
        <label htmlFor="invoice-select" className="text-sm font-medium text-ink">
          Invoice in this document ({invoices.length} found{reviewCount ? `, ${reviewCount} need review` : ""})
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <select id="invoice-select" className="input min-w-[12rem] flex-1" value={selected} onChange={(e) => onSelect(Number(e.target.value))}>
          {invoices.map((invoice, i) => (
            <option key={invoice.invoice_id} value={invoice.segment_index}>
              {`${i + 1} of ${invoices.length} · ${pages(invoice)} · ${String(invoice.fields.invoice_number?.value ?? "no number")}${invoice.needs_review ? " · needs review" : ""}${invoice.extracted ? "" : " · not extracted yet"}`}
            </option>
          ))}
        </select>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary px-3" disabled={index <= 0}
            onClick={() => onSelect(invoices[index - 1].segment_index)}>
            <ChevronLeft className="size-4" aria-hidden="true" /><span className="sr-only sm:not-sr-only">Previous invoice</span>
          </button>
          <button type="button" className="btn-secondary px-3" disabled={index >= invoices.length - 1}
            onClick={() => onSelect(invoices[index + 1].segment_index)}>
            <span className="sr-only sm:not-sr-only">Next invoice</span><ChevronRight className="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
