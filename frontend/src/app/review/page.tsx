"use client";
import Link from "next/link";
import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, Clock } from "lucide-react";
import { api } from "@/lib/api";
import { formatDateTime, formatMoney } from "@/lib/format";
import { EmptyState, ErrorBox, SkeletonRows } from "@/components/Feedback";
import { Pagination } from "@/components/Filters";
import { DocThumb } from "@/components/DocThumb";
import { PageHeader } from "@/components/ui";
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
  const first = queue.data?.items[0];

  return (
    <div>
      <PageHeader
        title="Review queue"
        eyebrow="Needs a person"
        description="Invoices with a missing, invalid or inconsistent value, oldest first. Every value links to the text it came from."
        actions={
          first ? (
            <Link href={`/documents/${first.id}`} className="btn-primary">
              Start with the oldest <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          ) : null
        }
      />
      <section className="card animate-fade-up" aria-label="Documents needing review">
        {queue.isPending ? (
          <SkeletonRows />
        ) : queue.isError ? (
          <ErrorBox message={errorMessage(queue.error)} onRetry={() => void queue.refetch()} />
        ) : queue.data.items.length === 0 ? (
          <EmptyState
            title="Nothing to review"
            icon={CheckCircle2}
            action={<Link href="/documents" className="btn-secondary">Upload invoices</Link>}
          >
            Every processed invoice has been checked.
          </EmptyState>
        ) : (
          <>
            <p className="mb-2 text-sm text-muted">
              <span className="num font-medium text-ink">{queue.data.total}</span> document(s) waiting.
            </p>
            <ol className="-mx-2 divide-y divide-line">
              {queue.data.items.map((doc, i) => (
                <li key={doc.id}>
                  <Link
                    href={`/documents/${doc.id}`}
                    className="focus-ring group flex items-center gap-4 rounded-xl px-2 py-3 transition-colors hover:bg-surface-2"
                  >
                    <span className="num w-6 shrink-0 text-right text-xs text-faint">{offset + i + 1}</span>
                    <DocThumb id={doc.id} mime={doc.mime_type} filename={doc.filename} pageCount={doc.page_count} status={doc.status} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-ink">{doc.filename}</span>
                      <span className="block text-xs text-muted sm:truncate">
                        {doc.invoice_count > 1
                          ? `${doc.invoice_count} invoices in this document`
                          : `${doc.invoice?.supplier_name ?? "Unknown supplier"} · ${doc.invoice?.invoice_number ?? "no number"} · `}
                        {doc.invoice_count > 1 ? null : (
                          <span className="num">{formatMoney(doc.invoice?.total, doc.invoice?.currency)}</span>
                        )}
                      </span>
                    </span>
                    <span className="hidden items-center gap-1.5 text-xs text-muted sm:flex">
                      <Clock className="size-3.5" aria-hidden="true" />
                      uploaded {formatDateTime(doc.created_at)}
                    </span>
                    <ArrowRight className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5 group-hover:text-ink" aria-hidden="true" />
                  </Link>
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
