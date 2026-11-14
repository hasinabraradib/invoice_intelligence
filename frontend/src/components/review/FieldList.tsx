"use client";
import { useState } from "react";
import type { FieldView } from "@/lib/api";
import { fieldAnchor, HEADER_FIELDS, type FieldKind } from "@/lib/fields";
import { displayValue, formatMoney } from "@/lib/format";
import { ConfidenceBar, FieldStatusBadge, VisionBadge } from "../Badges";
import { Image as ImageIcon, PencilLine, Quote } from "lucide-react";
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
    <ul className="-mx-1 divide-y divide-line" aria-label="Extracted fields">
      {HEADER_FIELDS.map(({ name, label, kind }) => {
        const field = fields[name] ?? { status: "missing" as const, confidence: 0, reasons: [] };
        return (
          <li key={name} id={fieldAnchor(name)} className="scroll-mt-24 px-1 py-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium uppercase tracking-[0.06em] text-muted">{label}</span>
              <div className="flex flex-wrap items-center gap-2">
                {field.source === "vision" ? <VisionBadge /> : null}
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
              <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
                <span className={`break-all text-lg font-medium ${field.value == null ? "text-faint" : "text-ink"} ${kind === "money" ? "num" : ""}`}>
                  {shown(field, kind, currency)}
                </span>
                {canEdit ? (
                  <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setEditing(name)}
                    aria-label={`Edit ${label}`}><PencilLine className="size-3.5" aria-hidden="true" /> Edit</button>
                ) : null}
              </div>
            )}
            {field.evidence ? (
              <button
                type="button"
                className="focus-ring group mt-2 flex w-full items-start gap-2 rounded-xl border border-line bg-surface-2 px-3 py-2 text-left text-xs text-ink-2 transition-colors hover:border-accent-line hover:bg-accent-soft disabled:hover:border-line disabled:hover:bg-surface-2"
                onClick={() => field.page && onEvidence(field.page)}
                disabled={!field.page}
                title={field.page ? `Show page ${field.page}` : undefined}
              >
                <Quote className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden="true" />
                <span className="flex-1 font-mono">{field.evidence}</span>
                {field.page ? <span className="shrink-0 font-medium text-accent-ink">p. {field.page}</span> : null}
              </button>
            ) : null}
            {field.source === "vision" ? (
              <p className="mt-2 flex items-start gap-1.5 text-xs text-accent-ink">
                <ImageIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                Read from the page image by the model because OCR could not read it; not checked against OCR text.
              </p>
            ) : null}
            {field.reasons && field.reasons.length > 0 ? (
              <ul className="mt-2 space-y-0.5 text-xs text-warning">
                {field.reasons.map((reason) => <li key={reason} className="flex gap-1.5"><span aria-hidden="true">·</span>{reason}</li>)}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
