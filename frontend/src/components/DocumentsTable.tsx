"use client";
import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isActive, type DocumentFilters } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import { StatusBadge } from "./Badges";
import { EmptyState, ErrorBox, SkeletonRows } from "./Feedback";
import { Filters, Pagination } from "./Filters";
import { ConfirmDialog } from "./Modal";
import { errorMessage, useToast } from "./Toast";
import { DocThumb } from "./DocThumb";
import { ProgressBar } from "./ui";
import { FileStack, RotateCcw, SlidersHorizontal } from "lucide-react";
import type { DocumentSummary } from "@/lib/api";

function pagesLabel(doc: DocumentSummary): string {
  const pages = doc.page_count ? `${doc.page_count} page${doc.page_count === 1 ? "" : "s"}` : null;
  const kind = /sheet|excel|csv/i.test(doc.mime_type) ? "spreadsheet" : null;
  const base = pages ?? kind ?? (doc.status === "failed" ? "not read" : "—");
  return doc.invoice_count > 1 ? `${base} · ${doc.invoice_count} invoices` : base;
}

/** Live progress of a running job: the stage in words and a smooth bar. */
function JobProgress({ doc }: { doc: DocumentSummary }) {
  const job = doc.job;
  if (!job) return null;
  const pages = job.pages_total ? job.pages_done / job.pages_total : null;
  const segments = job.segments_total ? job.segments_done / job.segments_total : null;
  const value = job.stage === "extracting" || job.stage === "embedding" ? (segments ?? 0.9) : (pages ?? 0.05);
  const words = [
    job.stage ?? job.status,
    job.pages_total ? `pages ${job.pages_done}/${job.pages_total}` : null,
    job.segments_total ? `invoices ${job.segments_done}/${job.segments_total}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <div className="w-36">
      <ProgressBar value={value} label={`Processing ${doc.filename}`} tone="bg-info-dot" />
      <p className="mt-1 truncate text-[11px] text-muted">{words}</p>
    </div>
  );
}

const LIMIT = 20;

export function DocumentsTable() {
  const [filters, setFilters] = useState<DocumentFilters>({});
  const [offset, setOffset] = useState(0);
  const [confirmRetry, setConfirmRetry] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const toast = useToast();
  const queryClient = useQueryClient();

  const documents = useQuery({
    queryKey: ["documents", filters, offset],
    queryFn: () => api.listDocuments({ ...filters, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    // Poll every 2 s only while something on screen is still queued or running.
    refetchInterval: (query) => (query.state.data?.items.some(isActive) ? 2000 : false),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["documents"] });
  const reprocess = useMutation({
    mutationFn: (id: string) => api.reprocess(id),
    onSuccess: () => {
      toast.success("Queued for processing again.");
      void refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const retryFailed = useMutation({
    mutationFn: api.retryFailed,
    onSuccess: (data) => {
      toast.success(`${data.requeued} failed document(s) queued again.`);
      setConfirmRetry(false);
      void refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const items = documents.data?.items ?? [];
  return (
    <section aria-labelledby="documents-heading" className="card space-y-5 p-0">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5">
        <div>
          <h2 id="documents-heading" className="font-semibold text-ink">All documents</h2>
          {documents.data ? <p className="num text-xs text-muted">{documents.data.total} in total</p> : null}
        </div>
        <button type="button" className="btn-secondary" onClick={() => setConfirmRetry(true)}>
          <RotateCcw className="size-4" aria-hidden="true" /> Retry all failed
        </button>
      </div>
      <div className="px-5">
        {/* Phones: filters behind a button, so the list is not pushed off screen. */}
        <button type="button" className="btn-secondary w-full md:hidden" aria-expanded={showFilters}
          aria-controls="docs-filters" onClick={() => setShowFilters((v) => !v)}>
          <SlidersHorizontal className="size-4" aria-hidden="true" /> Filters
          {Object.values(filters).some(Boolean) ? <span className="chip px-1.5 py-0">on</span> : null}
        </button>
        <div id="docs-filters" className={`${showFilters ? "mt-4 block" : "hidden"} md:mt-0 md:block`}>
          <Filters idPrefix="docs" value={filters} onChange={(next) => { setFilters(next); setOffset(0); }} />
        </div>
      </div>

      {documents.isPending ? (
        <div className="px-5 pb-5"><SkeletonRows /></div>
      ) : documents.isError ? (
        <div className="px-5 pb-5"><ErrorBox message={errorMessage(documents.error)} onRetry={() => void documents.refetch()} /></div>
      ) : items.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState title="No documents yet" icon={FileStack}
            action={<label htmlFor="file-input" className="btn-primary cursor-pointer">Upload invoices</label>}>
            Upload an invoice above, or change the filters.
          </EmptyState>
        </div>
      ) : (
        <>
          {/* Phones: a list of cards. */}
          <ul className="divide-y divide-line border-t border-line md:hidden">
            {items.map((doc) => (
              <li key={doc.id} className="flex gap-3 px-5 py-4">
                <DocThumb id={doc.id} mime={doc.mime_type} filename={doc.filename} pageCount={doc.page_count} status={doc.status} />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <Link href={`/documents/${doc.id}`} className="focus-ring min-w-0 truncate rounded text-sm font-medium text-ink hover:text-accent-ink">{doc.filename}</Link>
                    <StatusBadge status={doc.status} />
                  </div>
                  <p className="truncate text-xs text-muted">
                    {doc.invoice?.supplier_name ?? "—"} · {doc.invoice_count > 1 ? `${doc.invoice_count} invoices` : (doc.invoice?.invoice_number ?? "no number")}
                  </p>
                  <div className="flex items-center justify-between text-xs">
                    <span className="num font-medium text-ink">{doc.invoice_count > 1 ? "several" : formatMoney(doc.invoice?.total, doc.invoice?.currency)}</span>
                    <span className="text-muted">{formatDateTime(doc.created_at)}</span>
                  </div>
                  {doc.job && isActive(doc) ? <JobProgress doc={doc} /> : null}
                </div>
              </li>
            ))}
          </ul>
          {/* Tablets and up: a table. */}
          <div className="relative hidden overflow-x-auto md:block">
            <table className="w-full min-w-[52rem] text-left text-sm">
              <thead className="border-y border-line bg-surface-2/60 text-xs text-muted">
                <tr>
                  <th scope="col" className="py-2.5 pl-5 pr-3 font-medium">File</th>
                  <th scope="col" className="py-2.5 pr-3 font-medium">Status</th>
                  <th scope="col" className="py-2.5 pr-3 font-medium">Supplier</th>
                  <th scope="col" className="py-2.5 pr-3 font-medium">Invoice no.</th>
                  <th scope="col" className="py-2.5 pr-3 text-right font-medium">Total</th>
                  <th scope="col" className="py-2.5 pr-3 font-medium">Uploaded</th>
                  <th scope="col" className="py-2.5 pr-5"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map((doc) => (
                  <tr key={doc.id} className="transition-colors hover:bg-surface-2/50">
                    <td className="py-3 pl-5 pr-3">
                      <div className="flex max-w-72 items-center gap-3">
                        <DocThumb id={doc.id} mime={doc.mime_type} filename={doc.filename} pageCount={doc.page_count} status={doc.status} />
                        <div className="min-w-0">
                          <Link href={`/documents/${doc.id}`} className="focus-ring block truncate rounded font-medium text-ink hover:text-accent-ink">{doc.filename}</Link>
                          <span className="num text-xs text-muted">
                            {pagesLabel(doc)}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td className="py-3 pr-3">
                      <div className="flex flex-col items-start gap-1.5">
                        <StatusBadge status={doc.status} />
                        {doc.job && isActive(doc) ? <JobProgress doc={doc} /> : null}
                      </div>
                    </td>
                    <td className="max-w-44 truncate py-3 pr-3 text-ink-2">{doc.invoice?.supplier_name ?? "—"}</td>
                    <td className="py-3 pr-3 text-ink-2">
                      {doc.invoice_count > 1 ? `${doc.invoice?.invoice_number ?? "—"} +${doc.invoice_count - 1} more` : (doc.invoice?.invoice_number ?? "—")}
                    </td>
                    <td className="num py-3 pr-3 text-right font-medium text-ink">
                      {doc.invoice_count > 1 ? "several" : formatMoney(doc.invoice?.total, doc.invoice?.currency)}
                    </td>
                    <td className="whitespace-nowrap py-3 pr-3 text-muted">{formatDateTime(doc.created_at)}</td>
                    <td className="py-3 pr-5 text-right">
                      <button type="button" className="btn-ghost px-2.5 py-1.5 text-xs"
                        disabled={isActive(doc) || reprocess.isPending}
                        onClick={() => reprocess.mutate(doc.id)}
                        aria-label={`Reprocess ${doc.filename}`}>
                        <RotateCcw className="size-3.5" aria-hidden="true" /> Reprocess
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-5 pb-5">
            <Pagination total={documents.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </div>
        </>
      )}
      <ConfirmDialog
        open={confirmRetry}
        title="Retry all failed documents?"
        message="Every document whose last attempt failed will be queued again."
        confirmLabel="Retry all"
        busy={retryFailed.isPending}
        onConfirm={() => retryFailed.mutate()}
        onClose={() => setConfirmRetry(false)}
      />
    </section>
  );
}
