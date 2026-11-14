"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, query as toQuery, type DocumentFilters } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { MatchedByBadge, StatusBadge } from "@/components/Badges";
import { EmptyState, ErrorBox, SkeletonRows } from "@/components/Feedback";
import { Filters, Pagination } from "@/components/Filters";
import { Snippet } from "@/components/Snippet";
import { errorMessage } from "@/components/Toast";

const MODES = [
  { value: "hybrid", label: "Hybrid", hint: "words and meaning" },
  { value: "keyword", label: "Keyword", hint: "exact words" },
  { value: "semantic", label: "Semantic", hint: "similar meaning" },
] as const;
const LIMIT = 20;

export default function SearchPage() {
  return (
    <Suspense fallback={<SkeletonRows />}>
      <Search />
    </Suspense>
  );
}

function Search() {
  const params = useSearchParams();
  const router = useRouter();
  // The query and mode live in the URL, so a search can be shared or bookmarked.
  const q = params.get("q") ?? "";
  const mode = params.get("mode") ?? "hybrid";
  const [text, setText] = useState(q);
  const [filters, setFilters] = useState<DocumentFilters>({});
  const [offset, setOffset] = useState(0);

  const results = useQuery({
    queryKey: ["search", q, mode, filters, offset],
    enabled: q.trim() !== "",
    queryFn: () => api.search({ q, mode, ...filters, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  const go = (nextQ: string, nextMode: string) => {
    setOffset(0);
    router.replace(`/search${toQuery({ q: nextQ.trim(), mode: nextMode })}`);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Search</h1>
      <form role="search" className="card space-y-4" onSubmit={(e) => { e.preventDefault(); go(text, mode); }}>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label htmlFor="search-q" className="label">Search invoices</label>
            <input id="search-q" type="search" className="input" value={text} maxLength={200}
              placeholder='e.g. electricity, INV-2041, "office chairs"' onChange={(e) => setText(e.target.value)} />
          </div>
          <button type="submit" className="btn-primary">Search</button>
        </div>
        <fieldset>
          <legend className="label">Mode</legend>
          <div className="flex flex-wrap gap-2">
            {MODES.map((m) => (
              <label key={m.value} className={`focus-within:ring-2 focus-within:ring-accent cursor-pointer rounded-md border px-3 py-1.5 text-sm ${mode === m.value ? "border-accent bg-slate-900 text-white" : "border-line-strong bg-surface"}`}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value} className="sr-only"
                  onChange={() => go(text || q, m.value)} />
                {m.label} <span className="opacity-75">· {m.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <Filters idPrefix="search" value={filters} onChange={(next) => { setFilters(next); setOffset(0); }} />
      </form>

      <section aria-label="Results" aria-live="polite">
        {q.trim() === "" ? (
          <EmptyState title="Type something to search">Search finds words in documents, and with hybrid or semantic mode, similar meanings too.</EmptyState>
        ) : results.isPending ? (
          <SkeletonRows />
        ) : results.isError ? (
          <ErrorBox message={errorMessage(results.error)} onRetry={() => void results.refetch()} />
        ) : results.data.items.length === 0 ? (
          <EmptyState title="No matches">Try other words, another mode, or fewer filters.</EmptyState>
        ) : (
          <>
            <p className="mb-2 text-sm text-muted">{results.data.total} document(s)</p>
            <ul className="space-y-3">
              {results.data.items.map((hit) => (
                <li key={hit.document_id} className="card space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link href={`/documents/${hit.document_id}?invoice=${hit.segment_index ?? 0}&page=${hit.page_number}`} className="btn-link text-base">
                      {hit.filename} · page {hit.page_number}
                    </Link>
                    <span className="flex items-center gap-2">
                      <MatchedByBadge matchedBy={hit.matched_by} />
                      <StatusBadge status={hit.status} />
                    </span>
                  </div>
                  <p className="text-xs text-muted">
                    {hit.invoice_count > 1 && hit.segment_index != null ? `Invoice ${hit.segment_index + 1} of ${hit.invoice_count} · ` : ""}
                    {hit.supplier_name ?? "Unknown supplier"} · {hit.invoice_number ?? "no number"} · {formatMoney(hit.total, hit.currency)}
                  </p>
                  <Snippet text={hit.snippet} />
                </li>
              ))}
            </ul>
            <Pagination total={results.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </section>
    </div>
  );
}
