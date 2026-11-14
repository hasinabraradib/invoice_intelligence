"use client";
import type { DocumentFilters } from "@/lib/api";

const STATUSES = [
  ["", "Any status"],
  ["uploaded", "Queued"],
  ["processing", "Processing"],
  ["processed", "Processed"],
  ["needs_review", "Needs review"],
  ["failed", "Failed"],
  ["partially_failed", "Partly failed"],
] as const;

type Props = { value: DocumentFilters; onChange: (next: DocumentFilters) => void; idPrefix: string };

/** Status, needs-review, supplier and invoice date range: the same on every page. */
export function Filters({ value, onChange, idPrefix }: Props) {
  const set = (patch: Partial<DocumentFilters>) => onChange({ ...value, ...patch });
  const id = (name: string) => `${idPrefix}-${name}`;
  return (
    <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <legend className="sr-only">Filters</legend>
      <div>
        <label htmlFor={id("status")} className="label">Status</label>
        <select id={id("status")} className="input" value={value.status ?? ""}
          onChange={(e) => set({ status: e.target.value as DocumentFilters["status"] })}>
          {STATUSES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor={id("review")} className="label">Needs review</label>
        <select id={id("review")} className="input" value={value.needs_review ?? ""}
          onChange={(e) => set({ needs_review: e.target.value as DocumentFilters["needs_review"] })}>
          <option value="">Any</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      </div>
      <div>
        <label htmlFor={id("supplier")} className="label">Supplier</label>
        <input id={id("supplier")} className="input" value={value.supplier ?? ""}
          placeholder="e.g. acme" onChange={(e) => set({ supplier: e.target.value })} />
      </div>
      <div>
        <label htmlFor={id("from")} className="label">Invoice date from</label>
        <input id={id("from")} type="date" className="input" value={value.date_from ?? ""}
          onChange={(e) => set({ date_from: e.target.value })} />
      </div>
      <div>
        <label htmlFor={id("to")} className="label">Invoice date to</label>
        <input id={id("to")} type="date" className="input" value={value.date_to ?? ""}
          onChange={(e) => set({ date_to: e.target.value })} />
      </div>
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
    <nav aria-label="Pagination" className="flex items-center justify-between gap-2 pt-3 text-sm">
      <button type="button" className="btn-secondary" disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - limit))}>Previous</button>
      <span className="text-muted">Page {page} of {pages} · {total} total</span>
      <button type="button" className="btn-secondary" disabled={offset + limit >= total}
        onClick={() => onChange(offset + limit)}>Next</button>
    </nav>
  );
}
