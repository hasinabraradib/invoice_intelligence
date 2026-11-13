"use client";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, isActive, type DocumentResult, type InvoicePatch, type InvoiceResult } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { ApproveDialog } from "@/components/ApproveDialog";
import { StatusBadge } from "@/components/Badges";
import { ErrorBox, Skeleton, SkeletonRows } from "@/components/Feedback";
import { ConfirmDialog } from "@/components/Modal";
import { errorMessage, useToast } from "@/components/Toast";
import { FieldList } from "@/components/review/FieldList";
import { CorrectionsHistory, DuplicateLinks, IssuesPanel } from "@/components/review/IssuesPanel";
import { InvoiceSwitcher } from "@/components/review/InvoiceSwitcher";
import { LineItems } from "@/components/review/LineItems";
import { PageViewer } from "@/components/review/PageViewer";
import { useReviewer } from "@/components/review/useReviewer";

export default function ReviewPage() {
  // useSearchParams needs a Suspense boundary in the App Router.
  return (
    <Suspense fallback={<SkeletonRows rows={8} />}>
      <Review />
    </Suspense>
  );
}

function Review() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [reviewer, setReviewer] = useReviewer();
  const [approveOpen, setApproveOpen] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const viewer = useRef<HTMLElement>(null);
  const page = Number(params.get("page") ?? "1") || 1;
  const selected = Number(params.get("invoice") ?? "0") || 0; // segment_index

  const go = (invoice: number, n: number, scroll = false) => {
    router.replace(`/documents/${id}?invoice=${invoice}&page=${n}`, { scroll: false });
    if (scroll) viewer.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const setPage = (n: number, scroll = false) => go(selected, n, scroll);

  const document = useQuery({ queryKey: ["document", id], queryFn: () => api.getDocument(id) });
  const status = useQuery({
    queryKey: ["status", id],
    queryFn: () => api.getStatus(id),
    refetchInterval: (q) =>
      q.state.data && (["uploaded", "processing"].includes(q.state.data.status) ||
        ["queued", "running"].includes(q.state.data.job_status ?? "")) ? 2000 : false,
  });
  const state = status.data?.status;
  const done = state === "processed" || state === "needs_review";
  // The status is part of the key, so results reload by themselves when processing ends.
  const result = useQuery({
    queryKey: ["result", id, state],
    enabled: done,
    queryFn: async () => {
      try {
        return await api.getResult(id);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null; // not ready
        throw error;
      }
    },
  });
  const pages = useQuery({ queryKey: ["pages", id, state], queryFn: () => api.getPages(id) });
  const invoices = result.data?.invoices ?? [];
  const invoice = invoices.find((i) => i.segment_index === selected) ?? invoices[0];
  const invoiceId = invoice?.invoice_id;
  // Picking another invoice of a batch PDF jumps the page viewer to its first page.
  const selectInvoice = (segmentIndex: number) => {
    const next = invoices.find((i) => i.segment_index === segmentIndex);
    go(segmentIndex, next?.page_start ?? 1, true);
  };
  const corrections = useQuery({
    queryKey: ["corrections", invoiceId],
    enabled: Boolean(invoiceId),
    queryFn: () => api.getCorrections(invoiceId as string),
  });

  const afterChange = (data: InvoiceResult) => {
    // Replace just this invoice in the document's list of invoices.
    queryClient.setQueryData<DocumentResult | null>(["result", id, state], (old) =>
      old ? { ...old, invoices: old.invoices.map((i) => (i.invoice_id === data.invoice_id ? data : i)) } : old,
    );
    void queryClient.invalidateQueries({ queryKey: ["corrections", data.invoice_id] });
    void queryClient.invalidateQueries({ queryKey: ["status", id] });
    void queryClient.invalidateQueries({ queryKey: ["documents"] });
  };

  const patch = useMutation({
    mutationFn: (body: InvoicePatch) => api.patchInvoice(invoiceId as string, body),
    onSuccess: (data) => {
      afterChange(data);
      toast.success("Saved. Validation ran again.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const approve = useMutation({
    mutationFn: (args: { reviewer: string; reason?: string }) =>
      api.approve(invoiceId as string, args.reviewer, args.reason),
    onSuccess: (data, args) => {
      setReviewer(args.reviewer);
      setApproveOpen(false);
      afterChange(data);
      toast.success("Approved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reprocess = useMutation({
    mutationFn: () => api.reprocess(id),
    onSuccess: () => {
      setReprocessOpen(false);
      toast.success("Queued for processing again. Your corrections are kept.");
      void queryClient.invalidateQueries({ queryKey: ["status", id] });
      void queryClient.invalidateQueries({ queryKey: ["document", id] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const next = useMutation({
    mutationFn: () => api.listDocuments({ needs_review: "true", order: "oldest", limit: 5, offset: 0 }),
    onSuccess: (data) => {
      const following = data.items.find((d) => d.id !== id);
      if (following) router.push(`/documents/${following.id}`);
      else toast.success("The review queue is empty.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (document.isError) return <ErrorBox message={errorMessage(document.error)} onRetry={() => void document.refetch()} />;
  if (status.isError) return <ErrorBox message={errorMessage(status.error)} onRetry={() => void status.refetch()} />;
  if (document.isPending || status.isPending) return <SkeletonRows rows={8} />;

  const doc = document.data;
  const s = status.data;
  const errorCount = invoice?.issues.filter((i) => i.severity === "error").length ?? 0;
  const canEdit = Boolean(invoice) && reviewer.trim() !== "" && !patch.isPending;
  const busy = s ? isActive({ status: s.status, job: s.job_status ? { status: s.job_status } : null }) : false;

  const savePatch = (body: Omit<InvoicePatch, "corrected_by">) =>
    patch.mutate({ corrected_by: reviewer.trim(), ...body } as InvoicePatch);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm"><Link href="/" className="btn-link">← Documents</Link></p>
          <h1 className="truncate text-2xl font-semibold" title={doc.filename}>{doc.filename}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <StatusBadge status={s.status} />
            {busy ? (
              <span>
                {s.stage ?? s.job_status}
                {s.pages_total ? ` · page ${s.pages_done} of ${s.pages_total}` : ""}
                {s.segments_total ? ` · invoice ${s.segments_done} of ${s.segments_total}` : ""}
              </span>
            ) : null}
            {invoices.length > 1 ? <span>{invoices.length} invoices in this document</span> : null}
            {invoice?.reviewed_by ? (
              <span>Approved by {invoice.reviewed_by} · {formatDateTime(invoice.reviewed_at)}
                {invoice.review_override_reason ? ` (override: ${invoice.review_override_reason})` : ""}</span>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {invoice ? (
            <button type="button" className="btn-primary" onClick={() => setApproveOpen(true)} disabled={busy}>
              Approve
            </button>
          ) : null}
          <button type="button" className="btn-secondary" onClick={() => setReprocessOpen(true)} disabled={busy}>
            Reprocess
          </button>
          <button type="button" className="btn-secondary" onClick={() => next.mutate()} disabled={next.isPending}>
            Next in queue
          </button>
        </div>
      </div>

      {s.status === "failed" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-900">
          <span><strong>Processing failed:</strong> {s.error ?? "unknown error"}</span>
          <button type="button" className="btn-secondary" onClick={() => reprocess.mutate()} disabled={reprocess.isPending}>
            Retry
          </button>
        </div>
      ) : null}
      {s.error && s.status !== "failed" && s.error.startsWith("warning") ? (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">{s.error}</p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* min-w-0: grid columns otherwise grow to fit wide content (tables, images). */}
        <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
          {pages.isPending ? <Skeleton className="h-96 w-full" /> : (
            <PageViewer ref={viewer} documentId={id} pages={pages.data ?? []} page={page} onPage={(n) => setPage(n)} />
          )}
        </div>

        <div className="min-w-0 space-y-4">
          <InvoiceSwitcher invoices={invoices} selected={invoice?.segment_index ?? 0} onSelect={selectInvoice} />
          {invoice && !invoice.extracted ? (
            <p className="rounded-md bg-slate-100 p-3 text-sm text-slate-700">
              This invoice has not been extracted yet{busy ? "; processing is still running" : ""}.
            </p>
          ) : null}
          {invoice ? (
            <>
              <section aria-labelledby="fields-heading" className="card" id="fields">
                <div className="flex flex-wrap items-end justify-between gap-2">
                  <h2 id="fields-heading" className="text-lg font-semibold">Fields</h2>
                  <div>
                    <label htmlFor="reviewer-name" className="label">Your name (for the audit trail)</label>
                    <input id="reviewer-name" className="input py-1" value={reviewer}
                      onChange={(e) => setReviewer(e.target.value)} autoComplete="name" />
                  </div>
                </div>
                {reviewer.trim() === "" ? <p className="mt-2 text-xs text-slate-600">Enter your name to edit fields.</p> : null}
                <FieldList
                  fields={invoice.fields}
                  canEdit={canEdit}
                  savingField={patch.isPending ? "pending" : null}
                  onSave={(name, value) => savePatch({ [name]: value } as Omit<InvoicePatch, "corrected_by">)}
                  onEvidence={(n) => setPage(n, true)}
                />
              </section>
              <section aria-labelledby="items-heading" className="card">
                <h2 id="items-heading" className="mb-2 text-lg font-semibold">Line items</h2>
                <LineItems items={invoice.line_items} canEdit={canEdit} busy={patch.isPending}
                  onSave={(position, changes) => savePatch({ line_items: [{ position, ...changes }] } as Omit<InvoicePatch, "corrected_by">)} />
              </section>
              <section aria-labelledby="issues-heading" className="card space-y-3">
                <h2 id="issues-heading" className="text-lg font-semibold">
                  Issues {errorCount > 0 ? <span className="text-sm font-normal text-red-700">({errorCount} error{errorCount === 1 ? "" : "s"})</span> : null}
                </h2>
                <IssuesPanel issues={invoice.issues} />
                <DuplicateLinks duplicates={invoice.duplicates} />
              </section>
              <section aria-labelledby="history-heading" className="card">
                <h2 id="history-heading" className="mb-2 text-lg font-semibold">History</h2>
                {corrections.isPending ? <SkeletonRows rows={2} /> : <CorrectionsHistory corrections={corrections.data ?? []} />}
              </section>
            </>
          ) : result.isError ? (
            <ErrorBox message={errorMessage(result.error)} onRetry={() => void result.refetch()} />
          ) : busy || (done && result.isPending) ? (
            <div className="card"><p className="mb-3 text-sm text-slate-600">Extracting fields…</p><SkeletonRows rows={6} /></div>
          ) : (
            <div className="card text-sm text-slate-600">No extracted fields for this document.</div>
          )}
        </div>
      </div>

      {invoice && approveOpen ? (
        <ApproveDialog open errorCount={errorCount} defaultReviewer={reviewer} busy={approve.isPending}
          onClose={() => setApproveOpen(false)}
          onApprove={(name, reason) => approve.mutate({ reviewer: name, reason })} />
      ) : null}
      <ConfirmDialog open={reprocessOpen} title="Reprocess this document?"
        message="Parsing, OCR and extraction run again. Your corrected fields are kept; other values may change."
        confirmLabel="Reprocess" busy={reprocess.isPending}
        onConfirm={() => reprocess.mutate()} onClose={() => setReprocessOpen(false)} />
    </div>
  );
}
