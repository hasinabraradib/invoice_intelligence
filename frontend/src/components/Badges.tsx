import type { DocumentStatus } from "@/lib/api";

// Every badge shows its meaning in words, never by colour alone; the dot is decoration.
type Tone = "success" | "warning" | "info" | "danger" | "orange" | "neutral" | "accent";

export const TONE: Record<Tone, { badge: string; dot: string; bar: string }> = {
  success: { badge: "bg-success-soft text-success border-success-line", dot: "bg-success-dot", bar: "bg-success-dot" },
  warning: { badge: "bg-warning-soft text-warning border-warning-line", dot: "bg-warning-dot", bar: "bg-warning-dot" },
  info: { badge: "bg-info-soft text-info border-info-line", dot: "bg-info-dot", bar: "bg-info-dot" },
  danger: { badge: "bg-danger-soft text-danger border-danger-line", dot: "bg-danger-dot", bar: "bg-danger-dot" },
  orange: { badge: "bg-orange-soft text-orange border-orange-line", dot: "bg-orange-dot", bar: "bg-orange-dot" },
  neutral: { badge: "bg-neutral-soft text-neutral border-neutral-line", dot: "bg-neutral-dot", bar: "bg-neutral-dot" },
  accent: { badge: "bg-surface text-ink border-ink/50", dot: "bg-ink", bar: "bg-ink" },
};

export const DOCUMENT_STATUS: Record<DocumentStatus, { label: string; tone: Tone }> = {
  uploaded: { label: "Queued", tone: "neutral" },
  processing: { label: "Processing", tone: "info" },
  processed: { label: "Processed", tone: "success" },
  needs_review: { label: "Needs review", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  partially_failed: { label: "Partly failed", tone: "orange" },
};

const FIELD: Record<string, Tone> = { found: "success", missing: "neutral", uncertain: "warning", corrected: "info" };

const base =
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap";

export function Pill({ tone, children, title }: { tone: Tone; children: React.ReactNode; title?: string }) {
  return (
    <span className={`${base} ${TONE[tone].badge}`} title={title}>
      <span aria-hidden="true" className={`size-1.5 rounded-full ${TONE[tone].dot}`} />
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const { label, tone } = DOCUMENT_STATUS[status];
  return <Pill tone={tone}>{label}</Pill>;
}

export function FieldStatusBadge({ status }: { status: string }) {
  return <Pill tone={FIELD[status] ?? "neutral"}>{status}</Pill>;
}

/** Fields read from the page image by the vision model, not checked against OCR text. */
export function VisionBadge() {
  return (
    <Pill tone="accent" title="Read from the page image by the model; not checked against OCR text">
      read from image
    </Pill>
  );
}

export function MatchedByBadge({ matchedBy }: { matchedBy: string }) {
  const label = matchedBy === "both" ? "keyword + meaning" : matchedBy === "semantic" ? "meaning" : "keyword";
  return <Pill tone={matchedBy === "keyword" ? "neutral" : "accent"}>{label}</Pill>;
}

export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const color = pct >= 80 ? "bg-ink" : pct >= 60 ? "bg-warning-dot" : "bg-danger-dot";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${pct}%`}>
      <div
        className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-3"
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className={`h-full rounded-full ${color} transition-[width] duration-500`} style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs text-muted">{pct}%</span>
    </div>
  );
}
