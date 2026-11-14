"use client";
// One "Invoice date" control: empty by default ("Any date"), with presets and a calendar.
import * as Popover from "@radix-ui/react-popover";
import { useState } from "react";
import { CalendarDays, ChevronDown } from "lucide-react";
import { Calendar } from "./Calendar";
import { PRESETS, fromIso, rangeLabel, toIso, type Range } from "./dates";

type Props = { id?: string; value: Range; onChange: (next: Range) => void; label?: string };

export function DateRange({ id, value, onChange, label = "Invoice date" }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Range>(value);
  const active = !!(value.from || value.to);

  const pick = (iso: string) => {
    // First click starts a new range; the second closes it (in either order).
    if (!draft.from || draft.to) setDraft({ from: iso, to: undefined });
    else setDraft(iso < draft.from ? { from: iso, to: draft.from } : { from: draft.from, to: iso });
  };
  const apply = (next: Range) => {
    onChange(next);
    setOpen(false);
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        if (next) setDraft(value);
        setOpen(next);
      }}
    >
      <Popover.Trigger
        id={id}
        aria-label={`${label}: ${rangeLabel(value)}`}
        className={`input flex items-center gap-2 text-left data-[state=open]:border-ink ${active ? "border-ink/60" : ""}`}
      >
        <CalendarDays className="size-4 shrink-0 text-muted" aria-hidden="true" />
        <span className={`num min-w-0 flex-1 truncate ${active ? "text-ink" : "text-muted"}`}>{rangeLabel(value)}</span>
        <ChevronDown className="size-4 shrink-0 text-muted" aria-hidden="true" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className="menu flex max-w-[calc(100vw-1.5rem)] animate-fade-in flex-col gap-3 p-3 sm:flex-row"
          aria-label={label}
        >
          <ul className="flex flex-wrap gap-1 sm:w-36 sm:flex-col sm:border-r sm:border-line sm:pr-3" aria-label="Presets">
            <li>
              <button type="button" className="menu-item w-full" onClick={() => apply({})}>Any date</button>
            </li>
            {PRESETS.map((p) => (
              <li key={p.key}>
                <button type="button" className="menu-item w-full whitespace-nowrap" onClick={() => apply(p.range(new Date()))}>
                  {p.label}
                </button>
              </li>
            ))}
          </ul>
          <div>
            <Calendar mode="range" range={draft} onSelect={pick} />
            <p className="num mt-2 text-xs text-muted" aria-live="polite">
              {draft.from && !draft.to ? "Now pick the end date." : rangeLabel(draft)}
            </p>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" className="btn-ghost btn-sm" onClick={() => apply({})}>Clear</button>
              <button
                type="button"
                className="btn-primary btn-sm"
                onClick={() => apply(draft.from && !draft.to ? { from: draft.from, to: draft.from } : draft)}
              >
                Apply
              </button>
            </div>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** A single date: a text field (YYYY-MM-DD, validated by the caller) with a calendar button. */
export function DateField({
  id,
  value,
  onChange,
  inputProps,
}: {
  id: string;
  value: string;
  onChange: (iso: string) => void;
  inputProps?: React.InputHTMLAttributes<HTMLInputElement>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex gap-2">
      <input
        {...inputProps}
        id={id}
        type="text"
        inputMode="numeric"
        className="input num flex-1"
        value={value}
        placeholder="YYYY-MM-DD"
        onChange={(e) => onChange(e.target.value)}
      />
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger className="btn-secondary size-10 shrink-0 px-0" aria-label="Pick a date">
          <CalendarDays className="size-4" aria-hidden="true" />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content align="end" sideOffset={6} collisionPadding={12} className="menu animate-fade-in p-3">
            <Calendar
              mode="single"
              value={fromIso(value) ? value : toIso(new Date())}
              onSelect={(iso) => {
                onChange(iso);
                setOpen(false);
              }}
            />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  );
}
