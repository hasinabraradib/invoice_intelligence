import Link from "next/link";
import type { CorrectionView, InvoiceResult, IssueView } from "@/lib/api";
import { fieldAnchor } from "@/lib/fields";
import { formatDateTime } from "@/lib/format";
import { issueLabel } from "@/lib/issues";
import { AlertOctagon, AlertTriangle, CheckCircle2 } from "lucide-react";

/** Errors first, then warnings; each links to its field. */
export function IssuesPanel({ issues }: { issues: IssueView[] }) {
  if (issues.length === 0)
    return (
      <p className="flex items-center gap-2 text-sm text-success">
        <CheckCircle2 className="size-4" aria-hidden="true" /> No issues found.
      </p>
    );
  const sorted = [...issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1));
  return (
    <ul className="space-y-2">
      {sorted.map((issue, i) => (
        <li key={`${issue.code}-${issue.field}-${i}`}
          className={`rounded-2xl border p-3.5 text-sm ${issue.severity === "error" ? "border-danger-line bg-danger-soft" : "border-warning-line bg-warning-soft"}`}>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <span className="flex min-w-0 items-start gap-2">
              {issue.severity === "error" ? (
                <AlertOctagon className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
              ) : (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
              )}
              <span>
                <span className="font-medium text-ink" title={issue.code}>
                  {issueLabel(issue.code)} <span className={`text-xs font-normal ${issue.severity === "error" ? "text-danger" : "text-warning"}`}>· {issue.severity}</span>
                </span>
                <span className="block text-ink-2">{issue.message}</span>
              </span>
            </span>
            {issue.field ? <a href={`#${fieldAnchor(issue.field)}`} className="btn-link text-xs">{issue.field}</a> : null}
          </div>
          {issue.expected || issue.actual ? (
            <p className="mt-1 text-xs text-ink-2">
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
  if (corrections.length === 0) return <p className="text-sm text-muted">No changes yet.</p>;
  return (
    <ol className="space-y-2 text-sm">
      {[...corrections].reverse().map((c) => (
        <li key={c.id} className="relative border-l border-line pl-4 before:absolute before:-left-[4.5px] before:top-1.5 before:size-2 before:rounded-full before:bg-accent">
          <p>
            <strong>{c.field}</strong>: {c.old_value ?? "—"} → {c.new_value ?? "—"}
          </p>
          <p className="text-xs text-muted">{c.corrected_by} · {formatDateTime(c.corrected_at)}</p>
        </li>
      ))}
    </ol>
  );
}
