"use client";
import { useId, type ReactNode } from "react";

/** Wraps a button that may be disabled. When it is, the wrapper becomes focusable and
 * shows a short reason on hover and focus, and screen readers hear the same reason. */
export function DisabledHint({ disabled, reason, children }: { disabled: boolean; reason: string; children: ReactNode }) {
  const id = useId();
  // The wrapper is always rendered, so the button is never remounted (it keeps focus and
  // identity when it becomes enabled); only the hint and tab stop come and go.
  return (
    <span
      className="group/hint relative inline-flex rounded-full focus-ring"
      tabIndex={disabled ? 0 : undefined}
      aria-describedby={disabled ? id : undefined}
    >
      {children}
      {disabled ? (
        <span
          id={id}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-30 mb-2 w-max max-w-60 -translate-x-1/2 rounded-lg bg-accent px-2.5 py-1.5 text-xs text-on-accent opacity-0 transition-opacity group-hover/hint:opacity-100 group-focus/hint:opacity-100"
        >
          {reason}
        </span>
      ) : null}
    </span>
  );
}

/** An on/off switch (role="switch") in place of a browser checkbox. */
export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (next: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="focus-ring group inline-flex items-center gap-2.5 rounded-full text-sm text-ink-2"
    >
      <span
        aria-hidden="true"
        className={`relative h-5 w-9 rounded-full border transition-colors ${checked ? "border-accent bg-accent" : "border-line-strong bg-surface-3"}`}
      >
        <span
          className={`absolute top-1/2 size-3.5 -translate-y-1/2 rounded-full transition-[left] ${checked ? "left-[1.1rem] bg-on-accent" : "left-0.5 bg-surface"}`}
        />
      </span>
      {label}
    </button>
  );
}

export type Pastel = "butter" | "pink" | "sage" | "peri" | "peach";

const PASTEL: Record<Pastel, { bg: string; shape: string }> = {
  butter: { bg: "bg-butter", shape: "text-butter-deep" },
  pink: { bg: "bg-pink", shape: "text-pink-deep" },
  sage: { bg: "bg-sage", shape: "text-sage-deep" },
  peri: { bg: "bg-peri", shape: "text-peri-deep" },
  peach: { bg: "bg-peach", shape: "text-peach-deep" },
};

/** One large, faint abstract shape per card, drawn in a deeper shade of the card colour. */
function Shape({ kind }: { kind: "ring" | "capsule" | "quarter" | "arch" }) {
  const common = "pointer-events-none absolute opacity-60";
  switch (kind) {
    case "ring":
      return (
        <svg aria-hidden="true" viewBox="0 0 100 100" className={`${common} -right-8 -top-10 size-36`}>
          <circle cx="50" cy="50" r="36" fill="none" stroke="currentColor" strokeWidth="18" />
        </svg>
      );
    case "capsule":
      return (
        <svg aria-hidden="true" viewBox="0 0 100 100" className={`${common} -right-6 -top-6 size-32 rotate-[35deg]`}>
          <rect x="30" y="0" width="40" height="100" rx="20" fill="currentColor" />
        </svg>
      );
    case "quarter":
      return (
        <svg aria-hidden="true" viewBox="0 0 100 100" className={`${common} -right-2 -bottom-2 size-28`}>
          <path d="M100 0 A100 100 0 0 0 0 100 L100 100 Z" fill="currentColor" />
        </svg>
      );
    default:
      return (
        <svg aria-hidden="true" viewBox="0 0 100 100" className={`${common} -right-4 -bottom-6 size-32`}>
          <path d="M10 100 V55 a40 40 0 0 1 80 0 V100 H70 V55 a20 20 0 0 0 -40 0 V100 Z" fill="currentColor" />
        </svg>
      );
  }
}

const SHAPES = { butter: "capsule", pink: "ring", sage: "quarter", peri: "arch", peach: "ring" } as const;

/** A colour-blocked summary card: pastel background, dark text, one decorative shape. */
export function PastelCard({
  tone,
  className = "",
  children,
}: {
  tone: Pastel;
  className?: string;
  children: ReactNode;
}) {
  const t = PASTEL[tone];
  return (
    <div className={`relative isolate overflow-hidden rounded-card p-5 text-on-pastel ${t.bg} ${className}`}>
      <span className={t.shape}>
        <Shape kind={SHAPES[tone]} />
      </span>
      <div className="relative">{children}</div>
    </div>
  );
}
