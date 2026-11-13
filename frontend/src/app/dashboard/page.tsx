"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { assistant } from "@/lib/assistant";
import { ErrorBox, SkeletonRows } from "@/components/Feedback";
import { errorMessage } from "@/components/Toast";

const STATUS_LABELS: Record<string, string> = {
  uploaded: "Queued", processing: "Processing", processed: "Processed", needs_review: "Needs review",
  failed: "Failed", partially_failed: "Partly failed",
};

/** The numbers behind the assistant's greeting, at a glance. */
export default function DashboardPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: assistant.stats, refetchInterval: 10_000 });
  if (stats.isPending) return <SkeletonRows />;
  if (stats.isError) return <ErrorBox message={errorMessage(stats.error)} onRetry={() => void stats.refetch()} />;
  const s = stats.data;
  const tiles: [string, number, string][] = [
    ["Documents", s.documents, "/documents"],
    ["Invoices", s.invoices, "/documents"],
    ["Need review", s.invoices_need_review, "/review"],
    ["Jobs running", s.jobs_running + s.jobs_queued, "/documents"],
    ["Failed", s.documents_failed, "/documents"],
    ["Cross-checks", s.cross_checks, "/cross-check"],
  ];
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {tiles.map(([label, value, href]) => (
          <Link key={label} href={href} className="card focus-ring block hover:bg-slate-50">
            <dt className="text-sm text-slate-600">{label}</dt>
            <dd className="text-3xl font-semibold tabular-nums">{value}</dd>
          </Link>
        ))}
      </dl>
      <section className="card" aria-labelledby="status-heading">
        <h2 id="status-heading" className="mb-2 text-lg font-semibold">Documents by status</h2>
        <ul className="grid gap-1 text-sm sm:grid-cols-2">
          {Object.entries(s.documents_by_status).map(([status, count]) => (
            <li key={status} className="flex justify-between border-b border-slate-100 py-1">
              <Link className="btn-link" href={`/documents?status=${status}`}>{STATUS_LABELS[status] ?? status}</Link>
              <span className="tabular-nums">{count}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
