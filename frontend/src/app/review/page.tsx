"use client";
import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import { EmptyState, ErrorBox, SkeletonRows } from "@/components/Feedback";
import { Pagination } from "@/components/Filters";
import { errorMessage } from "@/components/Toast";

const LIMIT = 20;

/** Documents that need a human, oldest first. "Next" on the review page walks this list. */
export default function ReviewQueuePage() {
  const [offset, setOffset] = useState(0);
  const queue = useQuery({
    queryKey: ["documents", "queue", offset],
    queryFn: () => api.listDocuments({ needs_review: "true", order: "oldest", limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Review queue</h1>
        {queue.data?.items[0] ? (
          <Link href={`/documents/${queue.data.items[0].id}`} className="btn-primary">Start with the oldest</Link>
        ) : null}
      </div>
      <section className="card" aria-label="Documents needing review">
        {queue.isPending ? (
          <SkeletonRows />
        ) : queue.isError ? (
          <ErrorBox message={errorMessage(queue.error)} onRetry={() => void queue.refetch()} />
        ) : queue.data.items.length === 0 ? (
          <EmptyState title="Nothing to review">Every processed invoice has been checked.</EmptyState>
        ) : (
          <>
            <p className="mb-2 text-sm text-muted">{queue.data.total} document(s) waiting.</p>
            <ol className="divide-y divide-line">
              {queue.data.items.map((doc, i) => (
                <li key={doc.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <div className="min-w-0">
                    <Link href={`/documents/${doc.id}`} className="btn-link">
                      {offset + i + 1}. {doc.filename}
                    </Link>
                    <p className="text-xs text-muted">
                      {doc.invoice_count > 1
                        ? `${doc.invoice_count} invoices in this document`
                        : `${doc.invoice?.supplier_name ?? "Unknown supplier"} · ${doc.invoice?.invoice_number ?? "no number"} · ${formatMoney(doc.invoice?.total, doc.invoice?.currency)}`}
                    </p>
                  </div>
                  <span className="text-xs text-muted">uploaded {formatDateTime(doc.created_at)}</span>
                </li>
              ))}
            </ol>
            <Pagination total={queue.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </section>
    </div>
  );
}
