"use client";
import { useState } from "react";
import { canApprove } from "@/lib/validation";
import { Modal } from "./Modal";

type Props = {
  open: boolean;
  errorCount: number;
  defaultReviewer: string;
  busy?: boolean;
  onApprove: (reviewer: string, overrideReason?: string) => void;
  onClose: () => void;
};

/** Approve after review. While errors remain, an override reason is required. */
export function ApproveDialog({ open, errorCount, defaultReviewer, busy, onApprove, onClose }: Props) {
  const [reviewer, setReviewer] = useState(defaultReviewer);
  const [reason, setReason] = useState("");
  const allowed = canApprove(errorCount, reviewer, reason);

  return (
    <Modal open={open} title="Approve invoice" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (allowed) onApprove(reviewer.trim(), errorCount > 0 ? reason.trim() : undefined);
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor="approve-reviewer" className="label">
            Your name or email
          </label>
          <input
            id="approve-reviewer"
            className="input"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            autoComplete="name"
            required
          />
        </div>
        {errorCount > 0 ? (
          <div>
            <p className="mb-3 rounded-xl border border-danger-line bg-danger-soft p-3 text-sm text-danger">
              {errorCount} error{errorCount === 1 ? "" : "s"} still open. Fix them, or explain why
              this invoice is correct anyway.
            </p>
            <label htmlFor="approve-reason" className="label">
              Override reason (required)
            </label>
            <textarea
              id="approve-reason"
              className="input min-h-24"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={2000}
              required
            />
          </div>
        ) : (
          <p className="text-sm text-ink-2">No errors remain. Approving clears “needs review”.</p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={!allowed || busy}>
            {busy ? "Approving…" : "Approve"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
