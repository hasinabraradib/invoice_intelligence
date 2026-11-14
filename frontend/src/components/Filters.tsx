"use client";
import type { DocumentFilters } from "@/lib/api";
import { Search, X } from "lucide-react";
import { Select } from "./controls/Select";
import { DateRange } from "./controls/DateRange";
import { rangeLabel } from "./controls/dates";

const STATUSES = [
  ["", "Any status"],
  ["uploaded", "Queued"],
  ["processing", "Processing"],
  ["processed", "Processed"],
  ["needs_review", "Needs review"],
  ["failed", "Failed"],
  ["partially_failed", "Partly failed"],
] as const;

const REVIEW = [
  ["", "Review: any"],
  ["true", "Needs review"],
  ["false", "Reviewed or clean"],
] as const;

type Props = { value: DocumentFilters; onChange: (next: DocumentFilters) => void; idPrefix: string };

/** Status, needs-review, supplier and invoice date range in one compact toolbar, with
 * removable chips for what is active and "Clear all". The same on every page. */
export function Filters({ value, onChange, idPrefix }: Props) {
  const set = (patch: Partial<DocumentFilters>) => onChange({ ...value, ...patch });
  const id = (name: string) => `${idPrefix}-${name}`;
  const chips: { key: string; label: string; clear: () => void }[] = [];
  if (value.status) chips.push({ key: "status", label: `Status: ${STATUSES.find(([v]) => v === value.status)?.[1]}`, clear: () => set({ status: undefined }) });
  if (value.needs_review) chips.push({ key: "review", label: REVIEW.find(([v]) => v === value.needs_review)?.[1] ?? "", clear: () => set({ needs_review: undefined }) });
  if (value.supplier?.trim()) chips.push({ key: "supplier", label: `Supplier: ${value.supplier.trim()}`, clear: () => set({ supplier: undefined }) });
  if (value.date_from || value.date_to)
    chips.push({ key: "date", label: `Invoice date: ${rangeLabel({ from: value.date_from, to: value.date_to })}`, clear: () => set({ date_from: undefined, date_to: undefined }) });

  return (
    <fieldset className="space-y-3">
      <legend className="sr-only">Filters</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-[repeat(2,minmax(0,11rem))_minmax(0,1fr)_minmax(0,15rem)]">
        <div>
          <label htmlFor={id("status")} className="sr-only">Status</label>
          <Select id={id("status")} value={value.status ?? ""} options={STATUSES.map(([v, label]) => ({ value: v, label }))}
            onChange={(v) => set({ status: (v || undefined) as DocumentFilters["status"] })} />
        </div>
        <div>
          <label htmlFor={id("review")} className="sr-only">Needs review</label>
          <Select id={id("review")} value={value.needs_review ?? ""} options={REVIEW.map(([v, label]) => ({ value: v, label }))}
            onChange={(v) => set({ needs_review: (v || undefined) as DocumentFilters["needs_review"] })} />
        </div>
        <div className="relative">
          <label htmlFor={id("supplier")} className="sr-only">Supplier</label>
          <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted" aria-hidden="true" />
          <input id={id("supplier")} className="input pl-10" value={value.supplier ?? ""}
            placeholder="Supplier" onChange={(e) => set({ supplier: e.target.value })} />
        </div>
        <div>
          <DateRange id={id("date")} value={{ from: value.date_from, to: value.date_to }}
            onChange={(r) => set({ date_from: r.from, date_to: r.to })} />
        </div>
      </div>
      {chips.length > 0 ? (
        <ul className="flex flex-wrap items-center gap-1.5" aria-label="Active filters">
          {chips.map((c) => (
            <li key={c.key}>
              <button type="button" onClick={c.clear} className="focus-ring chip num border-ink/20 bg-surface hover:border-ink">
                {c.label}
                <X className="size-3.5" aria-hidden="true" />
                <span className="sr-only">(remove filter)</span>
              </button>
            </li>
          ))}
          <li>
            <button type="button" className="btn-link ml-1 text-xs" onClick={() => onChange({})}>Clear all</button>
          </li>
        </ul>
      ) : null}
    </fieldset>
  );
}

export function Pagination({ total, limit, offset, onChange }: {
  total: number; limit: number; offset: number; onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 pt-4 text-sm">
      <button type="button" className="btn-secondary" disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <span className="num text-muted">Page {page} of {pages} · {total} total</span>
      <button type="button" className="btn-secondary" disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}>Next</button>
    </nav>
  );
}
