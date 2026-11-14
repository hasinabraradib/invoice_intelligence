"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Clock, FlaskConical, Gauge, TriangleAlert, XCircle, type LucideIcon } from "lucide-react";
import { assistant, liveSummary, type Stats } from "@/lib/assistant";
import { PastelCard, type Pastel } from "@/components/controls/misc";
import { api } from "@/lib/api";
import { evaluationTiles } from "@/lib/evaluation";
import { issueLabel } from "@/lib/issues";
import { formatDateTime } from "@/lib/format";
import { DOCUMENT_STATUS, StatusBadge } from "@/components/Badges";
import { DailyBars, Donut, Sparkline, type Slice } from "@/components/charts";
import { EmptyState, ErrorBox, Skeleton, SkeletonCards } from "@/components/Feedback";
import { errorMessage } from "@/components/Toast";
import { CountUp, FileTypeIcon, PageHeader } from "@/components/ui";
import type { DocumentStatus } from "@/lib/api";

const IN_FLIGHT = new Set(["uploaded", "processing"]);
const DONUT_COLORS: Record<string, string> = {
  processed: "var(--sage)",
  needs_review: "var(--butter)",
  processing: "var(--peri)",
  uploaded: "var(--surface-3)",
  failed: "var(--pink)",
  partially_failed: "var(--peach)",
};

/** The numbers behind the assistant's greeting, at a glance. */
export default function DashboardPage() {
  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: assistant.stats,
    // Recent uploads show live status: poll faster while something is being processed.
    refetchInterval: (query) =>
      query.state.data?.recent_uploads?.some((u) => IN_FLIGHT.has(u.status)) ? 4_000 : 15_000,
  });
  const evaluation = useQuery({ queryKey: ["evaluation"], queryFn: api.latestEvaluation });

  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title="Dashboard"
        description={liveSummary(stats.data) ?? "What came in, what needs a person, and how well extraction is doing."}
        actions={<Link href="/documents" className="btn-primary">Upload invoices</Link>}
      />
      {stats.isPending ? (
        <div className="space-y-6">
          <SkeletonCards />
          <Skeleton className="h-72 rounded-card" />
        </div>
      ) : stats.isError ? (
        <ErrorBox message={errorMessage(stats.error)} onRetry={() => void stats.refetch()} />
      ) : (
        <Overview s={stats.data} evaluation={evaluation.data} evaluationPending={evaluation.isPending} />
      )}
    </div>
  );
}

type Eval = Awaited<ReturnType<typeof api.latestEvaluation>>;

function Overview({ s, evaluation, evaluationPending }: { s: Stats; evaluation: Eval | undefined; evaluationPending: boolean }) {
  const daily = s.daily ?? [];
  const seconds = s.avg_seconds_per_page;
  return (
    <div className="space-y-6">
      <section aria-label="Key numbers" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Processed"
          icon={CheckCircle2}
          tone="sage"
          value={s.documents_by_status.processed ?? 0}
          trend={daily.map((d) => d.processed)}
          href="/documents?status=processed"
          note="documents"
        />
        <Kpi
          label="Needs review"
          icon={TriangleAlert}
          tone="butter"
          value={s.invoices_need_review}
          trend={daily.map((d) => d.needs_review)}
          href="/review"
          note="invoices"
        />
        <Kpi
          label="Failed"
          icon={XCircle}
          tone="pink"
          value={s.documents_failed}
          trend={daily.map((d) => d.failed)}
          href="/documents?status=failed"
          note="documents"
        />
        <Kpi
          label="Time per page"
          icon={Clock}
          tone="peri"
          value={seconds}
          format={(n) => `${n.toFixed(1)} s`}
          trend={daily.map((d) => d.seconds_per_page ?? null)}
          note="average, last 14 days"
          empty="No finished jobs in the last 14 days"
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card animate-fade-up lg:col-span-2" aria-labelledby="daily-heading">
          <div className="mb-4 flex items-baseline justify-between gap-2">
            <h2 id="daily-heading" className="font-semibold text-ink">Documents per day</h2>
            <span className="text-xs text-muted">Last 14 days</span>
          </div>
          <DailyBars label="Documents uploaded per day" data={daily.map((d) => ({ date: d.date, value: d.uploaded }))} />
        </section>
        <EvaluationCard evaluation={evaluation} pending={evaluationPending} confidence={s.avg_extraction_confidence} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <StatusCard s={s} />
        <IssuesCard s={s} />
        <QueueCard s={s} />
      </div>

      <RecentCard s={s} />
    </div>
  );
}

function Kpi({
  label,
  icon: Icon,
  tone,
  value,
  trend,
  href,
  note,
  format,
  empty,
}: {
  label: string;
  icon: LucideIcon;
  tone: Pastel;
  value: number | null | undefined;
  trend: (number | null)[];
  href?: string;
  note: string;
  format?: (n: number) => string;
  empty?: string;
}) {
  const body = (
    <PastelCard tone={tone} className="h-full">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{label}</span>
        <span className="grid size-8 place-items-center rounded-full bg-on-pastel/10" aria-hidden="true">
          <Icon className="size-4" strokeWidth={1.75} />
        </span>
      </div>
      <div className="mt-5 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="num text-4xl font-semibold tracking-tight">
            {value === null || value === undefined ? "–" : <CountUp value={value} format={format} />}
          </div>
          <div className="mt-0.5 truncate text-xs text-on-pastel-muted">{value === null || value === undefined ? empty : note}</div>
        </div>
        <div className="w-24 shrink-0">
          <Sparkline data={trend} label={`${label} per day`} />
        </div>
      </div>
    </PastelCard>
  );
  return href ? (
    <Link href={href} className="card-hover focus-ring block animate-fade-up rounded-card">
      {body}
    </Link>
  ) : (
    <div className="animate-fade-up">{body}</div>
  );
}

function pct(v: unknown): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "–";
}

function EvaluationCard({ evaluation, pending, confidence }: { evaluation: Eval | undefined; pending: boolean; confidence: number | null | undefined }) {
  return (
    <section className="card flex animate-fade-up flex-col" aria-labelledby="eval-heading">
      <div className="mb-4 flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-full bg-peri text-on-pastel" aria-hidden="true">
          <FlaskConical className="size-4" strokeWidth={1.75} />
        </span>
        <h2 id="eval-heading" className="font-semibold text-ink">Latest evaluation</h2>
      </div>
      {pending ? (
        <Skeleton className="h-32" />
      ) : evaluation ? (
        <>
          <dl className="grid grid-cols-2 gap-4">
            <div>
              <dt className="text-xs text-muted">Field accuracy, clean test set</dt>
              <dd className="num mt-1 text-3xl font-semibold tracking-tight text-ink">{pct(evaluation.headline.field_accuracy)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Field accuracy, hard subset</dt>
              <dd className="num mt-1 text-3xl font-semibold tracking-tight text-ink">{pct(evaluation.headline.hard_field_accuracy)}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-muted">
            {evaluation.dataset} data, {evaluation.split} split, {evaluation.model}, {formatDateTime(evaluation.run_at)}.
          </p>
          <details className="group mt-auto pt-4">
            <summary className="btn-link cursor-pointer text-xs">More from this run</summary>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              {evaluationTiles(evaluation.headline).slice(2).map((t) => (
                <div key={t.label} title={t.hint} className="flex justify-between gap-2 border-b border-line py-1">
                  <dt className="text-muted">{t.label}</dt>
                  <dd className="num font-medium text-ink">{t.value}</dd>
                </div>
              ))}
            </dl>
          </details>
        </>
      ) : confidence !== null && confidence !== undefined ? (
        <>
          <dl>
            <dt className="text-xs text-muted">Average extraction confidence (no evaluation stored yet)</dt>
            <dd className="num mt-1 text-3xl font-semibold tracking-tight text-ink">{pct(confidence)}</dd>
          </dl>
          <p className="mt-3 text-xs text-muted">
            This is the model&apos;s own confidence, not measured accuracy. Run <code className="font-mono">make eval</code> for accuracy.
          </p>
        </>
      ) : (
        <p className="text-sm text-muted">
          No evaluation yet and no extracted invoices. Run <code className="font-mono">make eval</code> to measure accuracy.
        </p>
      )}
    </section>
  );
}

function StatusCard({ s }: { s: Stats }) {
  const slices: Slice[] = (Object.keys(DOCUMENT_STATUS) as DocumentStatus[]).map((status) => ({
    key: status,
    label: DOCUMENT_STATUS[status].label,
    value: s.documents_by_status[status] ?? 0,
    color: DONUT_COLORS[status],
  }));
  return (
    <section className="card animate-fade-up" aria-labelledby="status-heading">
      <h2 id="status-heading" className="mb-4 font-semibold text-ink">Documents by status</h2>
      {s.documents === 0 ? (
        <p className="text-sm text-muted">No documents yet.</p>
      ) : (
        <div className="flex flex-col items-center gap-5 sm:flex-row lg:flex-col xl:flex-row">
          <Donut data={slices} centerLabel="documents" centerValue={String(s.documents)} />
          <ul className="w-full space-y-1.5 text-sm">
            {slices.filter((d) => d.value > 0).map((d) => (
              <li key={d.key}>
                <Link href={`/documents?status=${d.key}`} className="focus-ring flex items-center justify-between gap-2 rounded-lg px-2 py-1 hover:bg-surface-2">
                  <span className="flex items-center gap-2 text-ink-2">
                    <span aria-hidden="true" className="size-2 rounded-full" style={{ background: d.color }} />
                    {d.label}
                  </span>
                  <span className="num font-medium text-ink">{d.value}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function IssuesCard({ s }: { s: Stats }) {
  const issues = s.top_issues ?? [];
  const max = Math.max(1, ...issues.map((i) => i.count));
  return (
    <section className="card animate-fade-up" aria-labelledby="issues-heading">
      <h2 id="issues-heading" className="mb-4 font-semibold text-ink">Most common issues</h2>
      {issues.length === 0 ? (
        <p className="text-sm text-muted">No issues found so far.</p>
      ) : (
        <ul className="space-y-3">
          {issues.map((issue) => (
            <li key={issue.code}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                <span className="truncate text-ink-2" title={issue.code}>{issueLabel(issue.code)}</span>
                <span className="num font-medium text-ink">{issue.count}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
                <div className="h-full rounded-full bg-ink transition-[width] duration-500" style={{ width: `${(issue.count / max) * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function QueueCard({ s }: { s: Stats }) {
  const queue = s.review_queue ?? [];
  return (
    <section className="card animate-fade-up" aria-labelledby="queue-heading">
      <div className="mb-4 flex items-baseline justify-between gap-2">
        <h2 id="queue-heading" className="font-semibold text-ink">Review queue</h2>
        <Link href="/review" className="btn-link text-xs">See all</Link>
      </div>
      {queue.length === 0 ? (
        <p className="text-sm text-muted">Nothing waiting for review.</p>
      ) : (
        <ol className="-mx-2 space-y-0.5">
          {queue.map((item) => (
            <li key={item.invoice_id}>
              <Link
                href={`/documents/${item.document_id}?invoice=${item.segment_index}`}
                className="focus-ring group flex items-start justify-between gap-3 rounded-xl px-2 py-2 hover:bg-surface-2"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-ink">
                    {item.supplier_name ?? item.filename}
                    {item.invoice_number ? <span className="font-normal text-muted"> · {item.invoice_number}</span> : null}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-warning">{issueLabel(item.main_issue)}</span>
                </span>
                <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-faint group-hover:text-ink" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function RecentCard({ s }: { s: Stats }) {
  const recent = s.recent_uploads ?? [];
  return (
    <section className="card animate-fade-up" aria-labelledby="recent-heading">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 id="recent-heading" className="font-semibold text-ink">Recent uploads</h2>
        <Link href="/documents" className="btn-link text-xs">All documents</Link>
      </div>
      {recent.length === 0 ? (
        <EmptyState title="No uploads yet" icon={Gauge} action={<Link href="/documents" className="btn-primary">Upload invoices</Link>}>
          Upload a PDF, photo or spreadsheet to get started.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line" aria-live="polite">
          {recent.map((doc) => (
            <li key={doc.id}>
              <Link href={`/documents/${doc.id}`} className="focus-ring -mx-2 flex items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-surface-2">
                <FileTypeIcon mime={doc.mime_type} filename={doc.filename} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">{doc.filename}</span>
                  <span className="block text-xs text-muted">
                    {formatDateTime(doc.created_at)}
                    {doc.page_count ? ` · ${doc.page_count} page${doc.page_count === 1 ? "" : "s"}` : ""}
                  </span>
                </span>
                <StatusBadge status={doc.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
