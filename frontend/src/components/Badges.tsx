import type { DocumentStatus } from "@/lib/api";

// Every badge shows its meaning in words, never by colour alone.
const DOCUMENT: Record<DocumentStatus, { label: string; style: string }> = {
  uploaded: { label: "Queued", style: "bg-slate-100 text-slate-800 border-slate-300" },
  processing: { label: "Processing", style: "bg-blue-50 text-blue-800 border-blue-300" },
  processed: { label: "Processed", style: "bg-green-50 text-green-800 border-green-300" },
  needs_review: { label: "Needs review", style: "bg-amber-50 text-amber-900 border-amber-300" },
  failed: { label: "Failed", style: "bg-red-50 text-red-800 border-red-300" },
};

const FIELD: Record<string, string> = {
  found: "bg-green-50 text-green-800 border-green-300",
  missing: "bg-slate-100 text-slate-700 border-slate-300",
  uncertain: "bg-amber-50 text-amber-900 border-amber-300",
  corrected: "bg-blue-50 text-blue-800 border-blue-300",
};

const base = "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap";

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const { label, style } = DOCUMENT[status];
  return <span className={`${base} ${style}`}>{label}</span>;
}

export function FieldStatusBadge({ status }: { status: string }) {
  return <span className={`${base} ${FIELD[status] ?? FIELD.missing}`}>{status}</span>;
}

export function MatchedByBadge({ matchedBy }: { matchedBy: string }) {
  const label = matchedBy === "both" ? "keyword + meaning" : matchedBy === "semantic" ? "meaning" : "keyword";
  return <span className={`${base} border-violet-300 bg-violet-50 text-violet-800`}>{label}</span>;
}

export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const color = pct >= 80 ? "bg-green-600" : pct >= 60 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${pct}%`}>
      <div
        className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-200"
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-600">{pct}%</span>
    </div>
  );
}
