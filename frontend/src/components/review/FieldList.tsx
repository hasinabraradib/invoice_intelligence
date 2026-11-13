"use client";
import { useState } from "react";
import type { FieldView } from "@/lib/api";
import { fieldAnchor, HEADER_FIELDS, type FieldKind } from "@/lib/fields";
import { displayValue, formatMoney } from "@/lib/format";
import { ConfidenceBar, FieldStatusBadge } from "../Badges";
import { FieldEditor } from "./FieldEditor";

type Props = {
  fields: Record<string, FieldView>;
  canEdit: boolean;
  savingField: string | null;
  onSave: (name: string, value: string | null) => void;
  onEvidence: (page: number) => void;
};

function shown(field: FieldView, kind: FieldKind, currency: unknown): string {
  if (kind === "money" && field.value != null) return formatMoney(String(field.value), currency ? String(currency) : null);
  return displayValue(field.value);
}

/** Right side of the review screen: every field with status, confidence and evidence. */
export function FieldList({ fields, canEdit, savingField, onSave, onEvidence }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const currency = fields.currency?.value;
  return (
    <ul className="divide-y divide-slate-100" aria-label="Extracted fields">
      {HEADER_FIELDS.map(({ name, label, kind }) => {
        const field = fields[name] ?? { status: "missing" as const, confidence: 0, reasons: [] };
        return (
          <li key={name} id={fieldAnchor(name)} className="scroll-mt-20 py-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium text-slate-600">{label}</span>
              <div className="flex items-center gap-3">
                <FieldStatusBadge status={field.status ?? "missing"} />
                <ConfidenceBar value={field.confidence ?? 0} />
              </div>
            </div>
            {editing === name ? (
              <FieldEditor
                id={`edit-${name}`}
                label={label}
                kind={kind}
                initial={field.value == null ? "" : String(field.value)}
                busy={savingField === name}
                onCancel={() => setEditing(null)}
                onSave={(value) => {
                  onSave(name, value);
                  setEditing(null);
                }}
              />
            ) : (
              <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                <span className="break-all text-base text-slate-900">{shown(field, kind, currency)}</span>
                {canEdit ? (
                  <button type="button" className="btn-link" onClick={() => setEditing(name)}
                    aria-label={`Edit ${label}`}>Edit</button>
                ) : null}
              </div>
            )}
            {field.evidence ? (
              <button
                type="button"
                className="focus-ring mt-1 block w-full rounded bg-slate-50 px-2 py-1 text-left text-xs text-slate-700 hover:bg-slate-100"
                onClick={() => field.page && onEvidence(field.page)}
                disabled={!field.page}
                title={field.page ? `Show page ${field.page}` : undefined}
              >
                “{field.evidence}” {field.page ? <span className="text-blue-700">· page {field.page}</span> : null}
              </button>
            ) : null}
            {field.reasons && field.reasons.length > 0 ? (
              <ul className="mt-1 list-inside list-disc text-xs text-amber-800">
                {field.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
