"use client";
import { useEffect, useRef } from "react";
import type { LedgerView } from "@/lib/api";
import { formatMoney } from "@/lib/format";

type Props = { ledger: LedgerView; sheet: string | null; row: number | null };

/** A ledger spreadsheet's rows as read with its column mapping. The row a cross-check
 * finding points to (?sheet=..&row=..) is highlighted and scrolled into view. */
export function LedgerPanel({ ledger, sheet, row }: Props) {
  const highlighted = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    highlighted.current?.scrollIntoView({ block: "center" });
  }, [sheet, row]);
  const sheets = ledger.sheets.filter((s) => s.is_ledger);
  if (sheets.length === 0) return null;
  return (
    <section className="card space-y-3" aria-labelledby="ledger-heading">
      <h2 id="ledger-heading" className="text-lg font-semibold">Ledger ({ledger.rows.total} rows)</h2>
      {sheets.map((s) => (
        <p key={s.name} className="text-xs text-slate-600">
          Sheet “{s.name}” ({s.source === "manual" ? "mapping fixed by hand" : "mapping detected"}):{" "}
          {Object.entries(s.columns).filter(([, c]) => c).map(([f, c]) => `${f} ← “${c}”`).join(", ")}
        </p>
      ))}
      <div className="relative max-h-[70vh] overflow-auto rounded border border-slate-200">
        <table className="w-full min-w-[40rem] text-left text-xs">
          <thead className="sticky top-0 bg-slate-50 text-slate-500">
            <tr>
              <th scope="col" className="px-2 py-1">Row</th>
              <th scope="col" className="px-2 py-1">Invoice no.</th>
              <th scope="col" className="px-2 py-1">Date</th>
              <th scope="col" className="px-2 py-1">Supplier</th>
              <th scope="col" className="px-2 py-1 text-right">Total</th>
              <th scope="col" className="px-2 py-1">Problems</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {ledger.rows.items.map((r) => {
              const active = r.sheet_name === sheet && r.row_number === row;
              return (
                <tr key={r.id} ref={active ? highlighted : undefined} aria-current={active ? "true" : undefined}
                  className={active ? "bg-yellow-100 outline outline-2 outline-yellow-400" : ""}>
                  <td className="px-2 py-1 text-slate-500">{sheets.length > 1 ? `${r.sheet_name} ` : ""}{r.row_number}</td>
                  <td className="px-2 py-1">{r.invoice_number ?? "—"}</td>
                  <td className="px-2 py-1">{r.invoice_date ?? "—"}</td>
                  <td className="px-2 py-1">{r.supplier_name ?? "—"}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{formatMoney(r.total, r.currency)}</td>
                  <td className="px-2 py-1 text-amber-800">{r.problems.join("; ")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
