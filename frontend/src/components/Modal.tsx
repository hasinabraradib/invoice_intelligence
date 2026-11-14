"use client";
// Accessible modal: focus moves into it, Tab stays inside, Escape closes, and focus
// returns to the button that opened it.
import { useEffect, useId, useRef, type ReactNode } from "react";

type Props = { open: boolean; title: string; onClose: () => void; children: ReactNode };

export function Modal({ open, title, onClose, children }: Props) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
        ) ?? [],
      ).filter((el) => !el.hasAttribute("disabled"));
    focusables()[0]?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex animate-fade-in items-end justify-center bg-black/30 p-4 backdrop-blur-[2px] sm:items-center dark:bg-black/60">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md animate-fade-up rounded-card border border-line bg-surface p-6 shadow-lift"
      >
        <h2 id={titleId} className="mb-3 text-lg font-semibold tracking-tight text-ink">
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
}

type ConfirmProps = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
};

export function ConfirmDialog({ open, title, message, confirmLabel, busy, onConfirm, onClose }: ConfirmProps) {
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <p className="mb-5 text-sm text-ink-2">{message}</p>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn-primary" disabled={busy} onClick={onConfirm}>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
