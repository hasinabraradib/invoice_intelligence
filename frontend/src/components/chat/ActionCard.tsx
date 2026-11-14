"use client";
import { useState } from "react";
import { CheckCircle2, CircleSlash, PencilLine } from "lucide-react";
import { assistant, type ActionView } from "@/lib/assistant";
import { errorMessage, useToast } from "../Toast";

/** A change the assistant proposed. Nothing happens until the user presses Confirm. It
 * should stand out, calmly: an accent card, not an alarm. */
export function ActionCard({ action, onChange }: { action: ActionView; onChange: (a: ActionView) => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [now] = useState(() => Date.now());
  const expired = action.status === "pending" && new Date(action.expires_at).getTime() < now;
  const status = expired ? "expired" : action.status;
  const minutes = Math.max(0, Math.round((new Date(action.expires_at).getTime() - now) / 60000));

  async function decide(kind: "confirm" | "cancel") {
    setBusy(true);
    try {
      const updated = await (kind === "confirm" ? assistant.confirm(action.id) : assistant.cancel(action.id));
      onChange(updated);
      toast.success(kind === "confirm" ? "Done." : "Cancelled.");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const pending = status === "pending";
  const tone = pending
    ? "border-accent-line bg-accent-soft"
    : status === "confirmed"
      ? "border-success-line bg-success-soft"
      : "border-line bg-surface-2";
  const Icon = pending ? PencilLine : status === "confirmed" ? CheckCircle2 : CircleSlash;
  return (
    <div className={`animate-fade-up rounded-2xl border p-4 text-sm ${tone}`} role="group" aria-label="Proposed change">
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={`grid size-8 shrink-0 place-items-center rounded-xl ${
            pending ? "bg-surface text-accent-ink shadow-soft" : status === "confirmed" ? "bg-surface text-success" : "bg-surface text-muted"
          }`}
        >
          <Icon className="size-4" strokeWidth={1.75} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-ink">{pending ? "Proposed change" : `Change ${status}`}</p>
          <p className="mt-0.5 text-ink-2">{action.summary}</p>
          {action.error ? <p className="mt-1 text-danger">{action.error}</p> : null}
          {pending ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy} onClick={() => void decide("confirm")}>
                Confirm
              </button>
              <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => void decide("cancel")}>
                Cancel
              </button>
              <span className="text-xs text-muted">Nothing changes until you confirm · expires in about {minutes} min</span>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
