"use client";
import { Select } from "@/components/controls/Select";
import { forwardRef } from "react";
import { fileUrl, pageImageUrl, type PageInfo } from "@/lib/api";
import { EmptyState } from "../Feedback";
import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";

type Props = { documentId: string; pages: PageInfo[]; page: number; onPage: (n: number) => void };

/** Left side of the review screen: page images, or a sheet's rows as a table. */
export const PageViewer = forwardRef<HTMLElement, Props>(function PageViewer(
  { documentId, pages, page, onPage },
  ref,
) {
  if (pages.length === 0) {
    return (
      <section ref={ref} aria-label="Document pages" className="card">
        <EmptyState title="No pages to show yet">
          Pages appear after processing. <a className="btn-link" href={fileUrl(documentId)} target="_blank" rel="noreferrer">Open the original file</a>
        </EmptyState>
      </section>
    );
  }
  const numbers = pages.map((p) => p.page_number);
  const current = pages.find((p) => p.page_number === page) ?? pages[0];
  const index = numbers.indexOf(current.page_number);

  return (
    <section ref={ref} aria-label="Document pages" className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary size-10 p-0" disabled={index <= 0}
            onClick={() => onPage(numbers[index - 1])} aria-label="Previous page"><ChevronLeft className="size-4" aria-hidden="true" /></button>
          <label htmlFor="page-select" className="sr-only">Page</label>
          <Select id="page-select" className="w-36" value={String(current.page_number)}
            onChange={(v) => onPage(Number(v))}
            options={pages.map((p) => ({ value: String(p.page_number), label: `${p.source === "sheet" ? "Sheet" : "Page"} ${p.page_number} of ${pages.length}` }))} />
          <button type="button" className="btn-secondary size-10 p-0" disabled={index >= pages.length - 1}
            onClick={() => onPage(numbers[index + 1])} aria-label="Next page"><ChevronRight className="size-4" aria-hidden="true" /></button>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted">
          <span className="chip">{current.source === "ocr" ? `OCR · ${Math.round(current.ocr_confidence ?? 0)}% confidence` : current.source === "vision" ? "read from image" : current.source.replace("_", " ")}</span>
          <a className="btn-link inline-flex items-center gap-1 text-xs" href={fileUrl(documentId)} target="_blank" rel="noreferrer">
            Original <ExternalLink className="size-3" aria-hidden="true" />
          </a>
        </div>
      </div>
      {current.has_image ? (
        // A plain <img>: the API renders and caches the PNG; next/image would add nothing.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={current.page_number}
          src={pageImageUrl(documentId, current.page_number)}
          alt={`Page ${current.page_number} of the document`}
          className="w-full animate-fade-in rounded-xl border border-line bg-white shadow-soft dark:opacity-95"
        />
      ) : (
        <SheetTable text={current.text} />
      )}
    </section>
  );
});

function SheetTable({ text }: { text: string }) {
  const rows = text.split("\n").map((line) => line.split("\t"));
  const width = Math.max(...rows.map((r) => r.length));
  return (
    <div className="relative max-h-[70vh] overflow-auto rounded-xl border border-line">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Spreadsheet rows</caption>
        <tbody className="divide-y divide-line">
          {rows.map((row, r) => (
            <tr key={r} className={row.length === width && r > 0 ? "" : "bg-surface-2 font-medium"}>
              {row.map((cell, c) => (
                <td key={c} className="whitespace-nowrap px-2 py-1" colSpan={row.length === 1 ? width : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
