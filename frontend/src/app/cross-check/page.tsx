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
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Cross-check</h1>
      <form className="card space-y-4" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <p className="text-sm text-slate-600">
          Compare invoices with a ledger spreadsheet (a list of invoices), or with another PDF such
          as a purchase order or a second copy. Values are never changed; differences are flagged
          for review.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <DocumentSelect id="doc-a" label="Document A" value={documentA} onChange={setDocumentA} documents={choices} />
          <DocumentSelect id="doc-b" label="Document B" value={documentB} onChange={setDocumentB} documents={choices} />
        </div>
        <button type="submit" className="btn-primary" disabled={!documentA || !documentB || documentA === documentB || run.isPending}>
          {run.isPending ? "Starting…" : "Run cross-check"}
        </button>
        {documents.isSuccess && choices.length < 2 ? (
          <p className="text-sm text-slate-600">Process at least two documents first.</p>
        ) : null}
      </form>

      {selected ? <CheckResult id={selected} /> : null}

      <section className="card" aria-labelledby="recent-heading">
        <h2 id="recent-heading" className="mb-2 text-lg font-semibold">Recent cross-checks</h2>
        {recent.isPending ? <SkeletonRows rows={3} /> : recent.isError ? (
          <ErrorBox message={errorMessage(recent.error)} />
        ) : recent.data.items.length === 0 ? (
          <p className="text-sm text-slate-600">None yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100 text-sm">
            {recent.data.items.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <Link href={`/cross-check?id=${c.id}`} className="btn-link" aria-current={c.id === selected ? "page" : undefined}>
                  {c.document_a.filename} ↔ {c.document_b.filename}
                </Link>
                <span className="text-xs text-slate-500">{c.status} · {formatDateTime(c.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DocumentSelect({ id, label, value, onChange, documents }: {
  id: string; label: string; value: string; onChange: (v: string) => void; documents: DocumentSummary[];
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="label">{label}</label>
      <select id={id} className="input" value={value} onChange={(e) => onChange(e.target.value)} required>
        <option value="">Choose a document…</option>
        {documents.map((d) => (
          <option key={d.id} value={d.id}>
            {d.filename}{d.invoice_count > 1 ? ` (${d.invoice_count} invoices)` : d.invoice_count === 0 ? " (ledger / no invoices)" : ""}
          </option>
        ))}
      </select>
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
    <section className="card space-y-4" aria-labelledby="result-heading">
      <div>
        <h2 id="result-heading" className="text-lg font-semibold">
          {c.document_a.filename} ↔ {c.document_b.filename}
        </h2>
        <p className="text-sm text-slate-600">
          {c.mode === "invoices_vs_ledger" ? "Invoices vs ledger" : c.mode === "invoices_vs_invoices" ? "Invoices vs invoices" : ""}
          {" · "}{c.status}{c.finished_at ? ` · ${formatDateTime(c.finished_at)}` : ""}
        </p>
      </div>
      {c.status === "failed" ? <ErrorBox message={c.error ?? "The cross-check failed."} /> : null}
      {c.status === "queued" || c.status === "running" ? <p className="text-sm text-slate-600" role="status">Comparing…</p> : null}
      {c.status === "succeeded" ? (
        <>
          <Summary check={c} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={showMatched} onChange={(e) => setShowMatched(e.target.checked)} className="focus-ring" />
            Show matched invoices too
          </label>
          <Findings check={c} showMatched={showMatched} />
        </>
      ) : null}
    </section>
  );
}

function Summary({ check }: { check: CrossCheckDetail }) {
  const s = check.summary;
  const ledger = check.mode === "invoices_vs_ledger";
  const tiles = [
    ["Matched", s.matched ?? 0, "text-green-800"],
    ["Mismatched", s.mismatched ?? 0, "text-red-800"],
    [ledger ? "Not in ledger" : "Only in A", s.missing_in_b ?? 0, "text-amber-900"],
    [ledger ? "Not in PDF" : "Only in B", s.missing_in_a ?? 0, "text-amber-900"],
    ...(ledger ? [["Ledger duplicates", s.duplicates_in_b ?? 0, "text-amber-900"] as const] : []),
  ] as const;
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
      {tiles.map(([label, value, color]) => (
        <div key={label} className="rounded-md border border-slate-200 p-3">
          <dt className="text-xs text-slate-600">{label}</dt>
          <dd className={`text-2xl font-semibold tabular-nums ${color}`}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Findings({ check, showMatched }: { check: CrossCheckDetail; showMatched: boolean }) {
  const rows = check.findings.filter((f) => showMatched || f.kind !== "matched");
  if (rows.length === 0) return <EmptyState title="No differences found">Every invoice matched.</EmptyState>;
  const severity = { error: "border-red-300 bg-red-50 text-red-800", warning: "border-amber-300 bg-amber-50 text-amber-900", info: "border-green-300 bg-green-50 text-green-800" };
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[52rem] text-left text-sm">
        <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
          <tr>
            <th scope="col" className="py-2 pr-3">Finding</th>
            <th scope="col" className="py-2 pr-3">Field</th>
            <th scope="col" className="py-2 pr-3">Value A</th>
            <th scope="col" className="py-2 pr-3">Value B</th>
            <th scope="col" className="py-2 pr-3">Evidence A</th>
            <th scope="col" className="py-2">Evidence B</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 align-top">
          {rows.map((f, i) => {
            const [a, b] = evidenceLinks(check, f);
            return (
              <tr key={i}>
                <td className="py-2 pr-3">
                  <span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${severity[f.severity]}`}>{kindLabel(f.kind)}</span>
                  <p className="mt-1 text-xs text-slate-600">{f.message}</p>
                </td>
                <td className="py-2 pr-3 text-slate-700">{f.field ?? "—"}</td>
                <td className="py-2 pr-3 tabular-nums">{f.value_a ?? "—"}</td>
                <td className="py-2 pr-3 tabular-nums">{f.value_b ?? "—"}</td>
                <td className="py-2 pr-3">{a ? <Link className="btn-link" href={a.href}>{a.label}</Link> : "—"}</td>
                <td className="py-2">{b ? <Link className="btn-link" href={b.href}>{b.label}</Link> : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
