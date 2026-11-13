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
      <div aria-live="polite" className="fixed inset-x-4 bottom-4 z-50 flex flex-col gap-2 sm:left-auto sm:w-96">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role={toast.kind === "error" ? "alert" : "status"}
            className={`flex items-start justify-between gap-3 rounded-md border px-4 py-3 text-sm shadow-md ${
              toast.kind === "error"
                ? "border-red-300 bg-red-50 text-red-900"
                : "border-green-300 bg-green-50 text-green-900"
            }`}
          >
            <span>{toast.message}</span>
            <button
              type="button"
              className="focus-ring rounded px-1 font-semibold"
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
