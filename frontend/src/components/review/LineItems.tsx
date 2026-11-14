"use client";
import { useState } from "react";
import type { LineItemView } from "@/lib/api";
import { fieldAnchor } from "@/lib/fields";
import { displayValue } from "@/lib/format";
import { FieldStatusBadge, VisionBadge } from "../Badges";
import { checkByKind } from "./FieldEditor";

const COLUMNS = [
  { name: "description", label: "Description", kind: "text" },
  { name: "quantity", label: "Qty", kind: "quantity" },
  { name: "unit_price", label: "Unit price", kind: "quantity" },
  { name: "line_total", label: "Line total", kind: "money" },
] as const;
type Column = (typeof COLUMNS)[number]["name"];

type Props = {
  items: LineItemView[];
  canEdit: boolean;
  busy: boolean;
  onSave: (position: number, changes: Partial<Record<Column, string | null>>) => void;
};

export function LineItems({ items, canEdit, busy, onSave }: Props) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});

  if (items.length === 0) return <p className="text-sm text-muted">No line items were found.</p>;

  const startEdit = (item: LineItemView) => {
    setDraft(Object.fromEntries(COLUMNS.map((c) => [c.name, item[c.name].value == null ? "" : String(item[c.name].value)])));
    setEditing(item.position);
  };
  const checks = Object.fromEntries(COLUMNS.map((c) => [c.name, checkByKind(c.kind, draft[c.name] ?? "")]));
  const valid = Object.values(checks).every((c) => c.ok);

  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th scope="col" className="py-2 pr-2">#</th>
            {COLUMNS.map((c) => <th key={c.name} scope="col" className="py-2 pr-2">{c.label}</th>)}
            <th scope="col" className="py-2"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {items.map((item) => (
            <tr key={item.position} id={fieldAnchor(`line_items[${item.position}]`)} className="scroll-mt-20 align-top">
              <td className="py-2 pr-2 text-muted">{item.position}</td>
              {COLUMNS.map((c) => {
                const cellId = `line-${item.position}-${c.name}`;
                const check = checks[c.name];
                return (
                  <td key={c.name} id={fieldAnchor(`line_items[${item.position}].${c.name}`)} className="py-2 pr-2">
                    {editing === item.position ? (
                      <>
                        <label htmlFor={cellId} className="sr-only">{c.label}, line {item.position}</label>
                        <input id={cellId} className="input py-1" value={draft[c.name] ?? ""}
                          inputMode={c.kind === "text" ? undefined : "decimal"}
                          aria-invalid={!check.ok}
                          onChange={(e) => setDraft({ ...draft, [c.name]: e.target.value })} />
                        {!check.ok ? <p className="text-xs text-danger">{check.error}</p> : null}
                      </>
                    ) : (
                      <div className="flex flex-col gap-1">
                        <span className={c.kind === "text" ? "text-ink" : "num text-ink"}>{displayValue(item[c.name].value)}</span>
                        {item[c.name].status !== "found" ? <FieldStatusBadge status={item[c.name].status ?? "missing"} /> : null}
                        {item[c.name].source === "vision" ? <VisionBadge /> : null}
                      </div>
                    )}
                  </td>
                );
              })}
              <td className="py-2 text-right whitespace-nowrap">
                {editing === item.position ? (
                  <div className="flex gap-1">
                    <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={!valid || busy}
                      onClick={() => {
                        const changes: Partial<Record<Column, string | null>> = {};
                        for (const c of COLUMNS) {
                          const check = checks[c.name];
                          const before = item[c.name].value == null ? null : String(item[c.name].value);
                          if (check.ok && check.value !== before) changes[c.name] = check.value;
                        }
                        if (Object.keys(changes).length > 0) onSave(item.position, changes);
                        setEditing(null);
                      }}>Save</button>
                    <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => setEditing(null)}>Cancel</button>
                  </div>
                ) : canEdit ? (
                  <button type="button" className="btn-link" onClick={() => startEdit(item)}
                    aria-label={`Edit line ${item.position}`}>Edit</button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
