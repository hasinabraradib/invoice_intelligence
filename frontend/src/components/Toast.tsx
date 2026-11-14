"use client";
// Small toast system: errors and confirmations, announced to screen readers.
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Toast = { id: number; kind: "error" | "success"; message: string };
type ToastApi = { error: (message: string) => void; success: (message: string) => void };

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((kind: Toast["kind"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all, { id, kind, message }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), 6000);
  }, []);
  const [api] = useState<ToastApi>(() => ({
    error: (message) => push("error", message),
    success: (message) => push("success", message),
  }));

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* Above the phone tab bar (bottom-20), bottom-right on larger screens. */}
      <div aria-live="polite" className="fixed inset-x-4 bottom-20 z-50 flex flex-col gap-2 sm:left-auto sm:w-96 md:bottom-6 md:right-6">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.kind === "error" ? "alert" : "status"}
            className="flex animate-fade-up items-start gap-3 rounded-2xl border border-line bg-surface px-4 py-3 text-sm text-ink shadow-lift"
          >
            <span
              aria-hidden="true"
              className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-semibold ${
                toast.kind === "error" ? "bg-danger-soft text-danger" : "bg-success-soft text-success"
              }`}
            >
              {toast.kind === "error" ? "!" : "✓"}
            </span>
            <span className="flex-1">{toast.message}</span>
            <button
              type="button"
              className="focus-ring -mr-1 rounded-lg px-1.5 text-muted hover:text-ink"
              aria-label="Dismiss"
              onClick={() => setToasts((all) => all.filter((t) => t.id !== toast.id))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used inside ToastProvider");
  return value;
}

/** Message to show for any thrown value (our ApiError carries the API's message). */
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}
