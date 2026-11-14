// Calendar dates as ISO "YYYY-MM-DD" strings, built in local time so a date never shifts
// by a day because of a timezone.

export function toIso(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function fromIso(iso: string | undefined | null): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso ?? "");
  if (!match) return null;
  const d = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return toIso(d) === iso ? d : null; // rejects 2026-02-31
}

export function addDays(d: Date, days: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
}

export function addMonths(d: Date, months: number): Date {
  const target = new Date(d.getFullYear(), d.getMonth() + months, 1);
  const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  return new Date(target.getFullYear(), target.getMonth(), Math.min(d.getDate(), last));
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

export function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}

/** Six weeks starting on the Monday on or before the 1st, so the grid never jumps. */
export function monthGrid(month: Date): Date[] {
  const first = startOfMonth(month);
  const offset = (first.getDay() + 6) % 7; // Monday = 0
  const start = addDays(first, -offset);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

export type Range = { from?: string; to?: string };

export type Preset = { key: string; label: string; range: (today: Date) => Range };

export const PRESETS: Preset[] = [
  { key: "this-month", label: "This month", range: (t) => ({ from: toIso(startOfMonth(t)), to: toIso(endOfMonth(t)) }) },
  {
    key: "last-month",
    label: "Last month",
    range: (t) => {
      const m = addMonths(startOfMonth(t), -1);
      return { from: toIso(m), to: toIso(endOfMonth(m)) };
    },
  },
  { key: "last-90", label: "Last 90 days", range: (t) => ({ from: toIso(addDays(t, -89)), to: toIso(t) }) },
  { key: "this-year", label: "This year", range: (t) => ({ from: `${t.getFullYear()}-01-01`, to: `${t.getFullYear()}-12-31` }) },
  { key: "last-year", label: "Last year", range: (t) => ({ from: `${t.getFullYear() - 1}-01-01`, to: `${t.getFullYear() - 1}-12-31` }) },
];

const fmt = (d: Date, withYear: boolean) =>
  d.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });

/** "Any date", "From 3 Mar 2026", "Until 9 Mar 2026" or "3 – 9 Mar 2026". */
export function rangeLabel(range: Range): string {
  const from = fromIso(range.from);
  const to = fromIso(range.to);
  if (from && to) {
    const sameYear = from.getFullYear() === to.getFullYear();
    return `${fmt(from, !sameYear)} – ${fmt(to, true)}`;
  }
  if (from) return `From ${fmt(from, true)}`;
  if (to) return `Until ${fmt(to, true)}`;
  return "Any date";
}
