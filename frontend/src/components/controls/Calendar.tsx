"use client";
// A small themed month calendar. One date gets Tab focus (roving tabindex); arrows move
// by day or week, PageUp/PageDown by month, Home/End to the week's ends, Enter/Space picks.
import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { addDays, addMonths, fromIso, monthGrid, toIso, type Range } from "./dates";

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

type Props =
  | { mode: "single"; value?: string; onSelect: (iso: string) => void; range?: never }
  | { mode: "range"; range: Range; onSelect: (iso: string) => void; value?: never };

export function Calendar(props: Props) {
  const today = toIso(new Date());
  const anchor = props.mode === "single" ? props.value : props.range.from ?? props.range.to;
  const [focused, setFocused] = useState<Date>(() => fromIso(anchor) ?? new Date());
  const [shouldFocus, setShouldFocus] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const month = new Date(focused.getFullYear(), focused.getMonth(), 1);
  const days = monthGrid(month);
  const focusedIso = toIso(focused);

  useEffect(() => {
    if (!shouldFocus) return;
    grid.current?.querySelector<HTMLButtonElement>(`[data-iso="${focusedIso}"]`)?.focus();
  }, [focusedIso, shouldFocus]);

  const move = (next: Date) => {
    setShouldFocus(true);
    setFocused(next);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const keys: Record<string, () => Date> = {
      ArrowLeft: () => addDays(focused, -1),
      ArrowRight: () => addDays(focused, 1),
      ArrowUp: () => addDays(focused, -7),
      ArrowDown: () => addDays(focused, 7),
      PageUp: () => addMonths(focused, -1),
      PageDown: () => addMonths(focused, 1),
      Home: () => addDays(focused, -((focused.getDay() + 6) % 7)),
      End: () => addDays(focused, 6 - ((focused.getDay() + 6) % 7)),
    };
    if (keys[e.key]) {
      e.preventDefault();
      move(keys[e.key]());
    }
  };

  const state = (iso: string) => {
    if (props.mode === "single") return { selected: iso === props.value, inRange: false, edge: iso === props.value };
    const { from, to } = props.range;
    const isEdge = iso === from || iso === to;
    const inRange = !!from && !!to && iso > from && iso < to;
    return { selected: isEdge, inRange, edge: isEdge };
  };

  return (
    <div className="w-[17.5rem] select-none">
      <div className="mb-2 flex items-center justify-between">
        <button type="button" className="btn-ghost btn-sm size-8 px-0" aria-label="Previous month" onClick={() => setFocused(addMonths(focused, -1))}>
          <ChevronLeft className="size-4" aria-hidden="true" />
        </button>
        <p className="text-sm font-semibold text-ink" aria-live="polite">
          {month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
        </p>
        <button type="button" className="btn-ghost btn-sm size-8 px-0" aria-label="Next month" onClick={() => setFocused(addMonths(focused, 1))}>
          <ChevronRight className="size-4" aria-hidden="true" />
        </button>
      </div>
      <div ref={grid} role="grid" aria-label="Calendar" onKeyDown={onKey}>
        <div role="row" className="grid grid-cols-7">
          {WEEKDAYS.map((d) => (
            <span key={d} role="columnheader" className="py-1 text-center text-[11px] font-medium text-muted">{d}</span>
          ))}
        </div>
        {Array.from({ length: 6 }, (_, week) => (
          <div role="row" key={week} className="grid grid-cols-7">
            {days.slice(week * 7, week * 7 + 7).map((d) => {
              const iso = toIso(d);
              const outside = d.getMonth() !== month.getMonth();
              const { selected, inRange } = state(iso);
              return (
                <span role="gridcell" key={iso} aria-selected={selected} className={inRange ? "bg-warning-soft" : ""}>
                  <button
                    type="button"
                    data-iso={iso}
                    tabIndex={iso === focusedIso ? 0 : -1}
                    onClick={() => {
                      setFocused(d);
                      props.onSelect(iso);
                    }}
                    aria-label={d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
                    aria-current={iso === today ? "date" : undefined}
                    className={`focus-ring num grid h-9 w-full place-items-center rounded-full text-sm transition-colors ${
                      selected
                        ? "bg-accent font-semibold text-on-accent"
                        : outside
                          ? "text-faint hover:bg-surface-2"
                          : "text-ink hover:bg-surface-2"
                    } ${iso === today && !selected ? "font-semibold underline decoration-2 underline-offset-4" : ""}`}
                  >
                    {d.getDate()}
                  </button>
                </span>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
