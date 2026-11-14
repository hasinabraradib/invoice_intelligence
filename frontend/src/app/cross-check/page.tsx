"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, crossChecks, type CrossCheckDetail, type DocumentSummary } from "@/lib/api";
import { evidenceLinks, kindLabel } from "@/lib/crosscheck";
import { formatDateTime } from "@/lib/format";
import { EmptyState, ErrorBox, SkeletonRows } from "@/components/Feedback";
import { errorMessage, useToast } from "@/components/Toast";
import { PageHeader } from "@/components/ui";
import { DocumentPicker } from "@/components/controls/DocumentPicker";
import { DisabledHint, PastelCard, Switch, type Pastel } from "@/components/controls/misc";
import { ArrowLeftRight, CheckCircle2, ChevronRight, CircleAlert, Copy, FileQuestion, GitCompareArrows } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const FINISHED = new Set(["processed", "needs_review", "partially_failed"]);

export default function CrossCheckPage() {
  return (
    <Suspense fallback={<SkeletonRows />}>
      <CrossCheck />
    </Suspense>
  );
}

function CrossCheck() {
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const selected = params.get("id");
  const [documentA, setDocumentA] = useState("");
  const [documentB, setDocumentB] = useState("");

  const documents = useQuery({
    queryKey: ["documents", "for-cross-check"],
    queryFn: () => api.listDocuments({ limit: 100, offset: 0 }),
  });
  const choices = (documents.data?.items ?? []).filter((d) => FINISHED.has(d.status));
  const recent = useQuery({ queryKey: ["cross-checks"], queryFn: () => crossChecks.list() });
  const run = useMutation({
    mutationFn: () => crossChecks.create(documentA, documentB),
    onSuccess: (check) => {
      void queryClient.invalidateQueries({ queryKey: ["cross-checks"] });
      router.replace(`/cross-check?id=${check.id}`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Cross-check"
        eyebrow="Compare documents"
        description="Compare invoices with a ledger spreadsheet (a list of invoices), or with another PDF such as a purchase order or a second copy. Values are never changed; differences are flagged for review."
      />
      <form className="card animate-fade-up space-y-5" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
          <DocumentSelect id="doc-a" label="Document A" prompt="Invoice PDF or batch" value={documentA} onChange={setDocumentA} documents={choices} exclude={documentB} />
          <span aria-hidden="true" className="mx-auto grid size-10 place-items-center rounded-full border border-line bg-surface-2 text-muted max-sm:rotate-90">
            <ArrowLeftRight className="size-4" />
          </span>
          <DocumentSelect id="doc-b" label="Document B" prompt="Ledger spreadsheet or second PDF" value={documentB} onChange={setDocumentB} documents={choices} exclude={documentA} />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <DisabledHint
            disabled={!documentA || !documentB || documentA === documentB}
            reason={documentA && documentA === documentB ? "Pick two different documents" : "Pick two documents to compare"}
          >
            <button type="submit" className="btn-primary" disabled={!documentA || !documentB || documentA === documentB || run.isPending}>
              <GitCompareArrows className="size-4" aria-hidden="true" />
              {run.isPending ? "Starting…" : "Run cross-check"}
            </button>
          </DisabledHint>
          {documents.isSuccess && choices.length < 2 ? (
            <p className="text-sm text-muted">
              Process at least two documents first. <Link href="/documents" className="btn-link">Upload documents</Link>
            </p>
          ) : null}
        </div>
      </form>

      {selected ? <CheckResult id={selected} /> : null}

      <section className="card" aria-labelledby="recent-heading">
        <h2 id="recent-heading" className="mb-3 font-semibold text-ink">Recent cross-checks</h2>
        {recent.isPending ? <SkeletonRows rows={3} /> : recent.isError ? (
          <ErrorBox message={errorMessage(recent.error)} />
        ) : recent.data.items.length === 0 ? (
          <p className="text-sm text-muted">None yet. Pick two documents above to run the first one.</p>
        ) : (
          <ul className="-mx-2 divide-y divide-line text-sm">
            {recent.data.items.map((c) => (
              <li key={c.id}>
                <Link
                  href={`/cross-check?id=${c.id}`}
                  aria-current={c.id === selected ? "page" : undefined}
                  className="focus-ring group flex flex-wrap items-center justify-between gap-2 rounded-xl px-2 py-2.5 hover:bg-surface-2 aria-[current=page]:bg-surface-2"
                >
                  <span className="min-w-0 truncate font-medium text-ink">
                    {c.document_a.filename} <span className="text-faint">↔</span> {c.document_b.filename}
                  </span>
                  <span className="flex items-center gap-2 text-xs text-muted">
                    {c.status} · {formatDateTime(c.created_at)}
                    <ChevronRight className="size-4 text-faint group-hover:text-ink" aria-hidden="true" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DocumentSelect({ id, label, prompt, value, onChange, documents, exclude }: {
  id: string; label: string; prompt: string; value: string; onChange: (v: string) => void; documents: DocumentSummary[]; exclude?: string;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="label">{label}</label>
      <DocumentPicker id={id} value={value} onChange={onChange} documents={documents} prompt={prompt} exclude={exclude} />
    </div>
  );
}

function CheckResult({ id }: { id: string }) {
  const [showMatched, setShowMatched] = useState(false);
  const check = useQuery({
    queryKey: ["cross-check", id],
    queryFn: () => crossChecks.get(id),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2000 : false),
  });
  if (check.isPending) return <SkeletonRows />;
  if (check.isError) return <ErrorBox message={errorMessage(check.error)} onRetry={() => void check.refetch()} />;
  const c = check.data;
  return (
    <section className="card animate-fade-up space-y-5" aria-labelledby="result-heading">
      <div>
        <h2 id="result-heading" className="font-semibold text-ink">
          {c.document_a.filename} ↔ {c.document_b.filename}
        </h2>
        <p className="text-sm text-muted">
          {c.mode === "invoices_vs_ledger" ? "Invoices vs ledger" : c.mode === "invoices_vs_invoices" ? "Invoices vs invoices" : ""}
          {" · "}{c.status}{c.finished_at ? ` · ${formatDateTime(c.finished_at)}` : ""}
        </p>
      </div>
      {c.status === "failed" ? <ErrorBox message={c.error ?? "The cross-check failed."} /> : null}
      {c.status === "queued" || c.status === "running" ? <p className="flex items-center gap-2 text-sm text-muted" role="status"><span className="size-2 animate-pulse rounded-full bg-info-dot" aria-hidden="true" /> Comparing…</p> : null}
      {c.status === "succeeded" ? (
        <>
          <Summary check={c} />
          <Switch checked={showMatched} onChange={setShowMatched} label="Show matched invoices too" />
          <Findings check={c} showMatched={showMatched} />
        </>
      ) : null}
    </section>
  );
}

function Summary({ check }: { check: CrossCheckDetail }) {
  const s = check.summary;
  const ledger = check.mode === "invoices_vs_ledger";
  const tiles: [string, number, Pastel, LucideIcon][] = [
    ["Matched", s.matched ?? 0, "sage", CheckCircle2],
    ["Mismatched", s.mismatched ?? 0, "pink", CircleAlert],
    [ledger ? "Not in ledger" : "Only in A", s.missing_in_b ?? 0, "butter", FileQuestion],
    [ledger ? "Not in PDF" : "Only in B", s.missing_in_a ?? 0, "butter", FileQuestion],
    ...(ledger ? [["Ledger duplicates", s.duplicates_in_b ?? 0, "peach", Copy] as [string, number, Pastel, LucideIcon]] : []),
  ];
  return (
    <ul aria-label="Summary" className={`grid grid-cols-2 gap-3 ${ledger ? "sm:grid-cols-3 xl:grid-cols-5" : "sm:grid-cols-4"}`}>
      {tiles.map(([label, value, tone, Icon]) => (
        <li key={label}>
          <PastelCard tone={tone} className="h-full p-4">
            <p className="flex items-center gap-1.5 text-xs font-medium">
              <Icon className="size-3.5" aria-hidden="true" /> {label}
            </p>
            <p className="num mt-2 text-3xl font-semibold tracking-tight">{value}</p>
          </PastelCard>
        </li>
      ))}
    </ul>
  );
}

function Findings({ check, showMatched }: { check: CrossCheckDetail; showMatched: boolean }) {
  const rows = check.findings.filter((f) => showMatched || f.kind !== "matched");
  if (rows.length === 0) return <EmptyState title="No differences found" icon={CheckCircle2}>Every invoice matched.</EmptyState>;
  const severity = { error: "border-danger-line bg-danger-soft text-danger", warning: "border-warning-line bg-warning-soft text-warning", info: "border-success-line bg-success-soft text-success" };
  return (
    <>
    {/* Phones: one card per finding. */}
    <ul className="space-y-3 xl:hidden">
      {rows.map((f, i) => {
        const [a, b] = evidenceLinks(check, f);
        return (
          <li key={i} className="space-y-2 rounded-2xl border border-line p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${severity[f.severity]}`}>{kindLabel(f.kind)}</span>
              {f.field ? <span className="text-xs text-muted">{f.field}</span> : null}
            </div>
            <p className="text-xs text-muted">{f.message}</p>
            <dl className="grid grid-cols-2 gap-2">
              <div><dt className="text-xs text-faint">Value A</dt><dd className="num text-ink">{f.value_a ?? "—"}</dd>
                {a ? <Link className="btn-link text-xs" href={a.href}>{a.label}</Link> : null}</div>
              <div><dt className="text-xs text-faint">Value B</dt><dd className="num text-ink">{f.value_b ?? "—"}</dd>
                {b ? <Link className="btn-link text-xs" href={b.href}>{b.label}</Link> : null}</div>
            </dl>
          </li>
        );
      })}
    </ul>
    <div className="relative hidden overflow-x-auto xl:block">
      <table className="w-full min-w-[52rem] text-left text-sm">
        <thead className="border-b border-line text-xs font-medium tracking-wide text-muted uppercase">
          <tr>
            <th scope="col" className="py-2 pr-3">Finding</th>
            <th scope="col" className="py-2 pr-3">Field</th>
            <th scope="col" className="py-2 pr-3">Value A</th>
            <th scope="col" className="py-2 pr-3">Value B</th>
            <th scope="col" className="py-2 pr-3">Evidence A</th>
            <th scope="col" className="py-2">Evidence B</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line align-top">
          {rows.map((f, i) => {
            const [a, b] = evidenceLinks(check, f);
            return (
              <tr key={i}>
                <td className="py-2.5 pr-3">
                  <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${severity[f.severity]}`}>{kindLabel(f.kind)}</span>
                  <p className="mt-1 text-xs text-muted">{f.message}</p>
                </td>
                <td className="py-2.5 pr-3 text-ink-2">{f.field ?? "—"}</td>
                <td className="num py-2.5 pr-3 text-ink">{f.value_a ?? "—"}</td>
                <td className="num py-2.5 pr-3 text-ink">{f.value_b ?? "—"}</td>
                <td className="py-2.5 pr-3">{a ? <Link className="btn-link" href={a.href}>{a.label}</Link> : "—"}</td>
                <td className="py-2.5">{b ? <Link className="btn-link" href={b.href}>{b.label}</Link> : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    </>
  );
}
