"use client";
import { useState } from "react";
import { CURRENCIES, type FieldKind } from "@/lib/fields";
import { Select } from "@/components/controls/Select";
import { DateField } from "@/components/controls/DateRange";
import { checkDate, checkMoney, checkQuantity, checkText, type Check } from "@/lib/validation";

export function checkByKind(kind: FieldKind | "quantity", input: string): Check {
  switch (kind) {
    case "money":
      return checkMoney(input);
    case "quantity":
      return checkQuantity(input);
    case "date":
      return checkDate(input);
    case "currency":
      return input === "" || CURRENCIES.includes(input)
        ? { ok: true, value: input || null }
        : { ok: false, error: "Choose a currency from the list." };
    default:
      return checkText(input);
  }
}

type Props = {
  id: string;
  label: string;
  kind: FieldKind | "quantity";
  initial: string;
  busy?: boolean;
  onSave: (value: string | null) => void;
  onCancel: () => void;
};

/** Inline editor: validates as you type and only saves valid values. */
export function FieldEditor({ id, label, kind, initial, busy, onSave, onCancel }: Props) {
  const [text, setText] = useState(initial);
  const check = checkByKind(kind, text);
  const errorId = `${id}-error`;
  const common = {
    id,
    className: "input",
    "aria-invalid": !check.ok,
    "aria-describedby": !check.ok ? errorId : undefined,
  };
  return (
    <form
      className="mt-2 space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (check.ok) onSave(check.value);
      }}
      onKeyDown={(e) => {
        // Escape inside an open menu or calendar closes only that popup (portals still bubble here).
        const inPopup = (e.target as HTMLElement).closest?.("[role=listbox],[role=grid],[data-radix-popper-content-wrapper]");
        if (e.key === "Escape" && !inPopup) onCancel();
      }}
    >
      <label htmlFor={id} className="sr-only">{label}</label>
      {kind === "currency" ? (
        <Select id={id} aria-label={label} value={text} onChange={setText}
          options={[{ value: "", label: "No currency" }, ...CURRENCIES.map((c) => ({ value: c, label: c }))]} />
      ) : kind === "date" ? (
        <DateField id={id} value={text} onChange={setText}
          inputProps={{ "aria-invalid": !check.ok, "aria-describedby": !check.ok ? errorId : undefined, autoFocus: true }} />
      ) : (
        <input {...common} type="text" inputMode={kind === "money" || kind === "quantity" ? "decimal" : undefined}
          value={text} onChange={(e) => setText(e.target.value)} autoFocus />
      )}
      {!check.ok ? <p id={errorId} className="text-xs text-danger">{check.error}</p> : null}
      <div className="flex gap-2">
        <button type="submit" className="btn-primary btn-sm" disabled={!check.ok || busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="btn-secondary btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
