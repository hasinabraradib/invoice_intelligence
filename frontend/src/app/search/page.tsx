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
import { FileTypeIcon, PageHeader } from "@/components/ui";
import { ChevronRight, Search as SearchIcon, SearchX } from "lucide-react";

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
    <div>
      <PageHeader title="Search" eyebrow="Find anything" description="By exact words, by meaning, or both. Results open on the page where the match is." />
      <form role="search" className="card animate-fade-up space-y-5" onSubmit={(e) => { e.preventDefault(); go(text, mode); }}>
        <div>
          <label htmlFor="search-q" className="label">Search invoices</label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative flex-1">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-faint" aria-hidden="true" />
              <input id="search-q" type="search" className="input rounded-full py-2.5 pl-10" value={text} maxLength={200}
                placeholder='e.g. electricity, INV-2041, "office chairs"' onChange={(e) => setText(e.target.value)} />
            </div>
            <button type="submit" className="btn-primary rounded-full px-5">Search</button>
          </div>
        </div>
        <fieldset>
          <legend className="label">Mode</legend>
          <div className="inline-flex flex-wrap gap-1 rounded-2xl border border-line bg-surface-2 p-1">
            {MODES.map((m) => (
              <label key={m.value} className={`cursor-pointer rounded-xl px-3 py-1.5 text-sm transition-colors focus-within:ring-2 focus-within:ring-accent ${mode === m.value ? "bg-surface font-medium text-ink shadow-soft" : "text-muted hover:text-ink"}`}>
                <input type="radio" name="mode" value={m.value} checked={mode === m.value} className="sr-only"
                  onChange={() => go(text || q, m.value)} />
                {m.label} <span className="text-faint">· {m.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <details className="group border-t border-line pt-4">
          <summary className="focus-ring inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md text-sm text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
            <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" /> Filters
          </summary>
          <div className="mt-4">
            <Filters idPrefix="search" value={filters} onChange={(next) => { setFilters(next); setOffset(0); }} />
          </div>
        </details>
      </form>

      <section aria-label="Results" aria-live="polite" className="mt-6">
        {q.trim() === "" ? (
          <EmptyState title="Type something to search" icon={SearchIcon}>Search finds words in documents, and with hybrid or semantic mode, similar meanings too.</EmptyState>
        ) : results.isPending ? (
          <SkeletonRows />
        ) : results.isError ? (
          <ErrorBox message={errorMessage(results.error)} onRetry={() => void results.refetch()} />
        ) : results.data.items.length === 0 ? (
          <EmptyState title="No matches" icon={SearchX}
            action={mode !== "hybrid" ? <button type="button" className="btn-secondary" onClick={() => go(q, "hybrid")}>Try hybrid search</button> : null}>
            Try other words, another mode, or fewer filters.
          </EmptyState>
        ) : (
          <>
            <p className="mb-3 text-sm text-muted"><span className="num font-medium text-ink">{results.data.total}</span> document(s)</p>
            <ul className="space-y-3">
              {results.data.items.map((hit, i) => (
                <li key={hit.document_id} className="card card-hover animate-fade-up space-y-3" style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <FileTypeIcon filename={hit.filename} />
                      <div className="min-w-0">
                        <Link href={`/documents/${hit.document_id}?invoice=${hit.segment_index ?? 0}&page=${hit.page_number}`} className="focus-ring block truncate rounded font-medium text-ink hover:text-accent-ink">
                          {hit.filename} · page {hit.page_number}
                        </Link>
                        <p className="truncate text-xs text-muted">
                          {hit.invoice_count > 1 && hit.segment_index != null ? `Invoice ${hit.segment_index + 1} of ${hit.invoice_count} · ` : ""}
                          {hit.supplier_name ?? "Unknown supplier"} · {hit.invoice_number ?? "no number"} · <span className="num">{formatMoney(hit.total, hit.currency)}</span>
                        </p>
                      </div>
                    </div>
                    <span className="flex flex-wrap items-center gap-2">
                      <MatchedByBadge matchedBy={hit.matched_by} />
                      <StatusBadge status={hit.status} />
                    </span>
                  </div>
                  <div className="rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 leading-relaxed">
                    <Snippet text={hit.snippet} />
                  </div>
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
