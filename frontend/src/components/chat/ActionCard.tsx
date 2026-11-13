"use client";
import { useState } from "react";
import { assistant, type ActionView } from "@/lib/assistant";
import { errorMessage, useToast } from "../Toast";

/** A change the assistant proposed. Nothing happens until the user presses Confirm. */
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

  const tone = status === "pending" ? "border-blue-300 bg-blue-50" : status === "confirmed" ? "border-green-300 bg-green-50" : "border-slate-300 bg-slate-50";
  return (
    <div className={`rounded-md border p-3 text-sm ${tone}`} role="group" aria-label="Proposed change">
      <p className="font-medium">{status === "pending" ? "Proposed change" : `Change ${status}`}</p>
      <p className="mt-1">{action.summary}</p>
      {action.error ? <p className="mt-1 text-red-800">{action.error}</p> : null}
      {status === "pending" ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy} onClick={() => void decide("confirm")}>Confirm</button>
          <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy} onClick={() => void decide("cancel")}>Cancel</button>
          <span className="text-xs text-slate-600">Nothing changes until you confirm · expires in about {minutes} min</span>
        </div>
      ) : null}
    </div>
  );
}
