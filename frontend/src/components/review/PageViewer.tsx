"use client";
import { forwardRef } from "react";
import { fileUrl, pageImageUrl, type PageInfo } from "@/lib/api";
import { EmptyState } from "../Feedback";

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
          <button type="button" className="btn-secondary px-2 py-1" disabled={index <= 0}
            onClick={() => onPage(numbers[index - 1])} aria-label="Previous page">‹</button>
          <label htmlFor="page-select" className="sr-only">Page</label>
          <select id="page-select" className="input w-auto py-1" value={current.page_number}
            onChange={(e) => onPage(Number(e.target.value))}>
            {pages.map((p) => (
              <option key={p.page_number} value={p.page_number}>
                {p.source === "sheet" ? `Sheet ${p.page_number}` : `Page ${p.page_number}`} of {pages.length}
              </option>
            ))}
          </select>
          <button type="button" className="btn-secondary px-2 py-1" disabled={index >= pages.length - 1}
            onClick={() => onPage(numbers[index + 1])} aria-label="Next page">›</button>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted">
          <span>{current.source === "ocr" ? `OCR · ${Math.round(current.ocr_confidence ?? 0)}% confidence` : current.source.replace("_", " ")}</span>
          <a className="btn-link text-xs" href={fileUrl(documentId)} target="_blank" rel="noreferrer">Original file</a>
        </div>
      </div>
      {current.has_image ? (
        // A plain <img>: the API renders and caches the PNG; next/image would add nothing.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={current.page_number}
          src={pageImageUrl(documentId, current.page_number)}
          alt={`Page ${current.page_number} of the document`}
          className="w-full rounded border border-line bg-surface"
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
    <div className="relative max-h-[70vh] overflow-auto rounded border border-line">
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
