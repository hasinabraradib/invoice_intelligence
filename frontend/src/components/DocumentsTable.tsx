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

const LIMIT = 20;

export function DocumentsTable() {
  const [filters, setFilters] = useState<DocumentFilters>({});
  const [offset, setOffset] = useState(0);
  const [confirmRetry, setConfirmRetry] = useState(false);
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

  return (
    <section aria-labelledby="documents-heading" className="card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="documents-heading" className="text-lg font-semibold">Documents</h2>
        <button type="button" className="btn-secondary" onClick={() => setConfirmRetry(true)}>
          Retry all failed
        </button>
      </div>
      <Filters idPrefix="docs" value={filters} onChange={(next) => { setFilters(next); setOffset(0); }} />

      {documents.isPending ? (
        <SkeletonRows />
      ) : documents.isError ? (
        <ErrorBox message={errorMessage(documents.error)} onRetry={() => void documents.refetch()} />
      ) : documents.data.items.length === 0 ? (
        <EmptyState title="No documents yet">Upload an invoice above, or change the filters.</EmptyState>
      ) : (
        <>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[56rem] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                <tr>
                  <th scope="col" className="py-2 pr-3">File</th>
                  <th scope="col" className="py-2 pr-3">Status</th>
                  <th scope="col" className="py-2 pr-3">Progress</th>
                  <th scope="col" className="py-2 pr-3">Supplier</th>
                  <th scope="col" className="py-2 pr-3">Invoice no.</th>
                  <th scope="col" className="py-2 pr-3 text-right">Invoices</th>
                  <th scope="col" className="py-2 pr-3 text-right">Total</th>
                  <th scope="col" className="py-2 pr-3">Review</th>
                  <th scope="col" className="py-2 pr-3">Uploaded</th>
                  <th scope="col" className="py-2"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {documents.data.items.map((doc) => (
                  <tr key={doc.id}>
                    <td className="max-w-56 truncate py-2 pr-3">
                      <Link href={`/documents/${doc.id}`} className="btn-link">{doc.filename}</Link>
                    </td>
                    <td className="py-2 pr-3"><StatusBadge status={doc.status} /></td>
                    <td className="py-2 pr-3 text-slate-600">
                      {doc.job && isActive(doc)
                        ? [
                            doc.job.stage ?? doc.job.status,
                            doc.job.pages_total ? `pages ${doc.job.pages_done}/${doc.job.pages_total}` : null,
                            doc.job.segments_total ? `invoices ${doc.job.segments_done}/${doc.job.segments_total}` : null,
                          ].filter(Boolean).join(" · ")
                        : "—"}
                    </td>
                    <td className="py-2 pr-3">{doc.invoice?.supplier_name ?? "—"}</td>
                    <td className="py-2 pr-3">
                      {doc.invoice_count > 1 ? `${doc.invoice?.invoice_number ?? "—"} +${doc.invoice_count - 1} more` : (doc.invoice?.invoice_number ?? "—")}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">{doc.invoice_count || "—"}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      {doc.invoice_count > 1 ? "several" : formatMoney(doc.invoice?.total, doc.invoice?.currency)}
                    </td>
                    <td className="py-2 pr-3">{doc.needs_review ? "⚑ Needs review" : "—"}</td>
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-600">{formatDateTime(doc.created_at)}</td>
                    <td className="py-2 text-right">
                      <button type="button" className="btn-secondary px-2 py-1 text-xs"
                        disabled={isActive(doc) || reprocess.isPending}
                        onClick={() => reprocess.mutate(doc.id)}
                        aria-label={`Reprocess ${doc.filename}`}>
                        Reprocess
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination total={documents.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
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
