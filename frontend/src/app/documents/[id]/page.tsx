"use client";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getLedger, isActive, type DocumentResult, type InvoicePatch, type InvoiceResult } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { ApproveDialog } from "@/components/ApproveDialog";
import { StatusBadge } from "@/components/Badges";
import { ErrorBox, Skeleton, SkeletonRows } from "@/components/Feedback";
import { ConfirmDialog } from "@/components/Modal";
import { errorMessage, useToast } from "@/components/Toast";
import { FieldList } from "@/components/review/FieldList";
import { CorrectionsHistory, DuplicateLinks, IssuesPanel } from "@/components/review/IssuesPanel";
import { InvoiceSwitcher } from "@/components/review/InvoiceSwitcher";
import { LedgerPanel } from "@/components/review/LedgerPanel";
import { LineItems } from "@/components/review/LineItems";
import { PageViewer } from "@/components/review/PageViewer";
import { useReviewer } from "@/components/review/useReviewer";
import { FileTypeIcon } from "@/components/ui";
import { DisabledHint } from "@/components/controls/misc";
import { ArrowRight, BadgeCheck, Check, ChevronLeft, RotateCcw } from "lucide-react";

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

  const document = useQuery({ queryKey: ["document", id, "detail"], queryFn: () => api.getDocument(id) });
  const status = useQuery({
    queryKey: ["status", id],
    queryFn: () => api.getStatus(id),
    refetchInterval: (q) =>
      q.state.data && (["uploaded", "processing"].includes(q.state.data.status) ||
        ["queued", "running"].includes(q.state.data.job_status ?? "")) ? 2000 : false,
  });
  const state = status.data?.status;
  useEffect(() => {
    if (state) void queryClient.invalidateQueries({ queryKey: ["document", id, "detail"] });
  }, [state, id, queryClient]);
  // Any finished state may have saved invoices (a partly failed batch keeps its good ones).
  const done = state !== undefined && state !== "uploaded" && state !== "processing";
  // The status is part of the key, so results reload by themselves when processing ends.
  const result = useQuery({
    queryKey: ["result", id, state],
    // Ledger spreadsheets have no invoices of their own: do not ask for a result.
    enabled: done && (document.data?.invoice_count ?? 0) > 0,
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
  const ledger = useQuery({
    queryKey: ["ledger", id, state],
    enabled: done && document.data?.mime_type !== "application/pdf",
    queryFn: () => getLedger(id),
  });
  const hasLedger = Boolean(ledger.data?.sheets.some((sheet) => sheet.is_ledger));
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
    mutationFn: (onlyFailed: boolean) => api.reprocess(id, onlyFailed),
    onSuccess: () => {
      setReprocessOpen(false);
      toast.success("Queued for processing again. Your corrections are kept.");
      void queryClient.invalidateQueries({ queryKey: ["status", id] });
      void queryClient.invalidateQueries({ queryKey: ["document", id, "detail"] });
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
    <div className="space-y-6">
      <div className="flex animate-fade-up flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Link href="/documents" className="btn-link mb-3 inline-flex items-center gap-1 text-muted hover:text-ink">
            <ChevronLeft className="size-4" aria-hidden="true" /> Documents
          </Link>
          <div className="flex min-w-0 items-center gap-3">
            <FileTypeIcon mime={doc.mime_type} filename={doc.filename} />
            <h1 className="truncate text-2xl font-semibold tracking-tight text-ink" title={doc.filename}>{doc.filename}</h1>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted">
            <StatusBadge status={s.status} />
            {busy ? (
              <span className="chip">
                {s.stage ?? s.job_status}
                {s.pages_total ? ` · page ${s.pages_done} of ${s.pages_total}` : ""}
                {s.segments_total ? ` · invoice ${s.segments_done} of ${s.segments_total}` : ""}
              </span>
            ) : null}
            {invoices.length > 1 ? <span className="chip">{invoices.length} invoices in this document</span> : null}
            {invoice?.reviewed_by ? (
              <span className="chip border-success-line bg-success-soft text-success">
                <BadgeCheck className="size-3.5" aria-hidden="true" />
                Approved by {invoice.reviewed_by} · {formatDateTime(invoice.reviewed_at)}
                {invoice.review_override_reason ? ` (override: ${invoice.review_override_reason})` : ""}
              </span>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-ghost" onClick={() => next.mutate()} disabled={next.isPending}>
            Next in queue <ArrowRight className="size-4" aria-hidden="true" />
          </button>
          <DisabledHint disabled={busy} reason="Wait until processing finishes">
            <button type="button" className="btn-secondary" onClick={() => setReprocessOpen(true)} disabled={busy}>
              <RotateCcw className="size-4" aria-hidden="true" /> Reprocess
            </button>
          </DisabledHint>
          {invoice ? (
            <DisabledHint disabled={busy} reason="Wait until processing finishes">
              <button type="button" className="btn-primary" onClick={() => setApproveOpen(true)} disabled={busy}>
                <Check className="size-4" aria-hidden="true" /> Approve
              </button>
            </DisabledHint>
          ) : null}
        </div>
      </div>

      {s.status === "partially_failed" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-orange-line bg-orange-soft p-4 text-sm text-orange">
          <span><strong>Some invoices failed</strong> (the others are saved): {s.error ?? "unknown error"}</span>
          <button type="button" className="btn-secondary" onClick={() => reprocess.mutate(true)} disabled={reprocess.isPending}>
            Retry failed invoices
          </button>
        </div>
      ) : null}
      {s.status === "failed" ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-danger-line bg-danger-soft p-4 text-sm text-danger">
          <span><strong>Processing failed:</strong> {s.error ?? "unknown error"}</span>
          <button type="button" className="btn-secondary" onClick={() => reprocess.mutate(false)} disabled={reprocess.isPending}>
            Retry
          </button>
        </div>
      ) : null}
      {s.error && s.status !== "failed" && s.error.startsWith("warning") ? (
        <p className="rounded-card border border-warning-line bg-warning-soft p-4 text-sm text-warning">{s.error}</p>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        {/* min-w-0: grid columns otherwise grow to fit wide content (tables, images). */}
        <div className="min-w-0 lg:sticky lg:top-6 lg:self-start">
          {pages.isPending ? <Skeleton className="h-96 w-full" /> : (
            <PageViewer ref={viewer} documentId={id} pages={pages.data ?? []} page={page} onPage={(n) => setPage(n)} />
          )}
        </div>

        <div className="min-w-0 space-y-6">
          <InvoiceSwitcher invoices={invoices} selected={invoice?.segment_index ?? 0} onSelect={selectInvoice} />
          {invoice && !invoice.extracted ? (
            <p className="rounded-card border border-line bg-surface-2 p-4 text-sm text-ink-2">
              This invoice has not been extracted yet{busy ? "; processing is still running" : ""}.
            </p>
          ) : null}
          {invoice ? (
            <>
              <section aria-labelledby="fields-heading" className="card" id="fields">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <h2 id="fields-heading" className="font-semibold text-ink">Fields</h2>
                  <div className="w-full sm:w-56">
                    <label htmlFor="reviewer-name" className="label text-xs">Your name (for the audit trail)</label>
                    <input id="reviewer-name" className="input" value={reviewer}
                      onChange={(e) => setReviewer(e.target.value)} autoComplete="name" />
                  </div>
                </div>
                {reviewer.trim() === "" ? <p className="mt-2 text-xs text-muted">Enter your name to edit fields.</p> : null}
                <FieldList
                  fields={invoice.fields}
                  canEdit={canEdit}
                  savingField={patch.isPending ? "pending" : null}
                  onSave={(name, value) => savePatch({ [name]: value } as Omit<InvoicePatch, "corrected_by">)}
                  onEvidence={(n) => setPage(n, true)}
                />
              </section>
              <section aria-labelledby="items-heading" className="card">
                <h2 id="items-heading" className="mb-3 font-semibold text-ink">Line items</h2>
                <LineItems items={invoice.line_items} canEdit={canEdit} busy={patch.isPending}
                  onSave={(position, changes) => savePatch({ line_items: [{ position, ...changes }] } as Omit<InvoicePatch, "corrected_by">)} />
              </section>
              <section aria-labelledby="issues-heading" className="card space-y-3">
                <h2 id="issues-heading" className="font-semibold text-ink">
                  Issues {errorCount > 0 ? <span className="text-sm font-normal text-danger">({errorCount} error{errorCount === 1 ? "" : "s"})</span> : null}
                </h2>
                <IssuesPanel issues={invoice.issues} />
                <DuplicateLinks duplicates={invoice.duplicates} />
              </section>
              <section aria-labelledby="history-heading" className="card">
                <h2 id="history-heading" className="mb-3 font-semibold text-ink">History</h2>
                {corrections.isPending ? <SkeletonRows rows={2} /> : <CorrectionsHistory corrections={corrections.data ?? []} />}
              </section>
            </>
          ) : result.isError ? (
            <ErrorBox message={errorMessage(result.error)} onRetry={() => void result.refetch()} />
          ) : hasLedger && ledger.data ? (
            <LedgerPanel ledger={ledger.data} sheet={params.get("sheet")} row={params.get("row") ? Number(params.get("row")) : null} />
          ) : busy || (done && result.isPending) ? (
            <div className="card"><p className="mb-3 flex items-center gap-2 text-sm text-muted"><span className="size-2 animate-pulse rounded-full bg-info-dot" aria-hidden="true" /> Extracting fields…</p><SkeletonRows rows={6} /></div>
          ) : (
            <div className="card text-sm text-muted">No extracted fields for this document.</div>
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
        onConfirm={() => reprocess.mutate(false)} onClose={() => setReprocessOpen(false)} />
    </div>
  );
}
