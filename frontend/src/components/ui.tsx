"use client";
// Small shared building blocks for the design system (see globals.css for the tokens).
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { FileImage, FileSpreadsheet, FileText, type LucideIcon } from "lucide-react";

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string;
  description?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-col gap-4 animate-fade-up sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1.5">{eyebrow}</p> : null}
        <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[1.75rem]">{title}</h1>
        {description ? <p className="mt-1.5 max-w-2xl text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

const MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeMotion(onChange: () => void) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const query = window.matchMedia(MOTION_QUERY);
  query.addEventListener?.("change", onChange);
  return () => query.removeEventListener?.("change", onChange);
}

/** True when the user asked the system for less motion (also true on the server and in
 * test environments without matchMedia, so nothing animates there). */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeMotion,
    () => !window.matchMedia || window.matchMedia(MOTION_QUERY).matches,
    () => true,
  );
}

/** A number that counts up to its value (no animation when motion is reduced). The final
 * value is what screen readers get; the animation is hidden from them. */
export function CountUp({
  value,
  format = (n) => Math.round(n).toLocaleString(),
  duration = 700,
}: {
  value: number;
  format?: (n: number) => string;
  duration?: number;
}) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(value);
  const from = useRef(0);
  useEffect(() => {
    if (reduced || typeof requestAnimationFrame === "undefined") return;
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(origin + (value - origin) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
      else from.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value, duration, reduced]);
  return (
    <>
      <span aria-hidden="true">{format(reduced ? value : shown)}</span>
      <span className="sr-only">{format(value)}</span>
    </>
  );
}

export function ProgressBar({ value, label, tone = "bg-accent" }: { value: number; label: string; tone?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3"
    >
      <div className={`h-full rounded-full ${tone} transition-[width] duration-500 ease-out`} style={{ width: `${pct}%` }} />
    </div>
  );
}

const KIND: { test: RegExp; icon: LucideIcon; label: string; tint: string }[] = [
  { test: /pdf/i, icon: FileText, label: "PDF", tint: "bg-danger-soft text-danger" },
  { test: /(image|png|jpe?g|tiff?)/i, icon: FileImage, label: "Image", tint: "bg-info-soft text-info" },
  { test: /(sheet|excel|xlsx?|csv)/i, icon: FileSpreadsheet, label: "Spreadsheet", tint: "bg-success-soft text-success" },
];

/** A file-type tile from the MIME type or file name (the label is for screen readers). */
export function FileTypeIcon({ mime, filename, size = "md" }: { mime?: string | null; filename?: string; size?: "sm" | "md" }) {
  const key = `${mime ?? ""} ${filename ?? ""}`;
  const kind = KIND.find((k) => k.test.test(key)) ?? { icon: FileText, label: "File", tint: "bg-surface-2 text-muted" };
  const Icon = kind.icon;
  const box = size === "sm" ? "size-8 rounded-lg" : "size-10 rounded-xl";
  return (
    <span className={`grid shrink-0 place-items-center ${box} ${kind.tint}`} role="img" aria-label={kind.label}>
      <Icon className={size === "sm" ? "size-4" : "size-5"} strokeWidth={1.75} aria-hidden="true" />
    </span>
  );
}
