import Link from "next/link";
import type { CorrectionView, InvoiceResult, IssueView } from "@/lib/api";
import { fieldAnchor } from "@/lib/fields";
import { formatDateTime } from "@/lib/format";

/** Errors first, then warnings; each links to its field. */
export function IssuesPanel({ issues }: { issues: IssueView[] }) {
  if (issues.length === 0) return <p className="text-sm text-green-800">No issues found.</p>;
  const sorted = [...issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1));
  return (
    <ul className="space-y-2">
      {sorted.map((issue, i) => (
        <li key={`${issue.code}-${issue.field}-${i}`}
          className={`rounded-md border p-3 text-sm ${issue.severity === "error" ? "border-red-300 bg-red-50" : "border-amber-300 bg-amber-50"}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-medium">
              <span className="uppercase">{issue.severity}</span> · {issue.message}
            </span>
            {issue.field ? <a href={`#${fieldAnchor(issue.field)}`} className="btn-link text-xs">{issue.field}</a> : null}
          </div>
          {issue.expected || issue.actual ? (
            <p className="mt-1 text-xs text-slate-700">
              {issue.expected ? <>Expected <strong>{issue.expected}</strong>. </> : null}
              {issue.actual ? <>Found <strong>{issue.actual}</strong>.</> : null}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function DuplicateLinks({ duplicates }: { duplicates: InvoiceResult["duplicates"] }) {
  if (duplicates.length === 0) return null;
  return (
    <ul className="space-y-1 text-sm">
      {duplicates.map((d) => (
        <li key={`${d.document_id}-${d.code}`}>
          {d.code === "DUPLICATE_INVOICE" ? "Same supplier and invoice number: " : "Same supplier, date and total: "}
          <Link className="btn-link" href={`/documents/${d.document_id}`}>open the other document</Link>
        </li>
      ))}
    </ul>
  );
}

export function CorrectionsHistory({ corrections }: { corrections: CorrectionView[] }) {
  if (corrections.length === 0) return <p className="text-sm text-slate-600">No changes yet.</p>;
  return (
    <ol className="space-y-2 text-sm">
      {[...corrections].reverse().map((c) => (
        <li key={c.id} className="border-l-2 border-slate-300 pl-3">
          <p>
            <strong>{c.field}</strong>: {c.old_value ?? "—"} → {c.new_value ?? "—"}
          </p>
          <p className="text-xs text-slate-500">{c.corrected_by} · {formatDateTime(c.corrected_at)}</p>
        </li>
      ))}
    </ol>
  );
}
