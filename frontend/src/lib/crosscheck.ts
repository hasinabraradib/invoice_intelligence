// Helpers for showing cross-check findings.
import type { CrossCheckView, FindingView } from "./api";

export type Evidence = { label: string; href: string } | null;

/** Where each side of a finding comes from: an invoice page, or a ledger sheet row. */
export function evidenceLinks(check: Pick<CrossCheckView, "document_a" | "document_b">, f: FindingView): [Evidence, Evidence] {
  const a: Evidence =
    f.invoice_a_id != null
      ? {
          label: `invoice ${(f.segment_a ?? 0) + 1}${f.page_a ? `, page ${f.page_a}` : ""}`,
          href: `/documents/${check.document_a.id}?invoice=${f.segment_a ?? 0}&page=${f.page_a ?? 1}`,
        }
      : null;
  let b: Evidence = null;
  if (f.row_b != null) {
    b = {
      label: `${f.sheet_b ?? "sheet"}, row ${f.row_b}`,
      href: `/documents/${check.document_b.id}?${new URLSearchParams({ sheet: f.sheet_b ?? "", row: String(f.row_b) })}`,
    };
  } else if (f.invoice_b_id != null) {
    b = {
      label: `invoice ${(f.segment_b ?? 0) + 1}${f.page_b ? `, page ${f.page_b}` : ""}`,
      href: `/documents/${check.document_b.id}?invoice=${f.segment_b ?? 0}&page=${f.page_b ?? 1}`,
    };
  }
  return [a, b];
}

const KIND_LABELS: Record<string, string> = {
  matched: "Matched",
  amount_mismatch: "Amount differs",
  date_mismatch: "Date differs",
  currency_mismatch: "Currency differs",
  field_mismatch: "Field differs",
  line_item_mismatch: "Line differs",
  line_missing_in_a: "Line only in B",
  line_missing_in_b: "Line only in A",
  missing_in_ledger: "Not in ledger",
  missing_in_pdf: "Not in PDF",
  missing_in_a: "Only in B",
  missing_in_b: "Only in A",
  duplicate_in_ledger: "Duplicate in ledger",
};

export const kindLabel = (kind: string) => KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
