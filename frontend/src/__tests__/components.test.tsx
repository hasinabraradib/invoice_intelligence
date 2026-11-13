import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApproveDialog } from "@/components/ApproveDialog";
import { FieldStatusBadge, StatusBadge } from "@/components/Badges";

describe("status badges", () => {
  it("shows the status in words", () => {
    render(<StatusBadge status="needs_review" />);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it.each(["found", "missing", "uncertain", "corrected"])("field status %s has text", (status) => {
    render(<FieldStatusBadge status={status} />);
    expect(screen.getByText(status)).toBeInTheDocument();
  });
});

describe("approve dialog", () => {
  it("approves at once when there are no errors", async () => {
    const onApprove = vi.fn();
    render(<ApproveDialog open errorCount={0} defaultReviewer="bo" onApprove={onApprove} onClose={() => {}} />);
    expect(screen.queryByLabelText(/override reason/i)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("bo", undefined);
  });

  it("requires an override reason while errors remain", async () => {
    const onApprove = vi.fn();
    render(<ApproveDialog open errorCount={2} defaultReviewer="bo" onApprove={onApprove} onClose={() => {}} />);
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(screen.getByText(/2 errors still open/)).toBeInTheDocument();
    expect(approve).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/override reason/i), "  ");
    expect(approve).toBeDisabled(); // whitespace is not a reason

    await userEvent.type(screen.getByLabelText(/override reason/i), "Confirmed by phone");
    expect(approve).toBeEnabled();
    await userEvent.click(approve);
    expect(onApprove).toHaveBeenCalledWith("bo", "Confirmed by phone");
  });

  it("closes on Escape and needs a reviewer name", async () => {
    const onClose = vi.fn();
    render(<ApproveDialog open errorCount={0} defaultReviewer="" onApprove={() => {}} onClose={onClose} />);
    expect(screen.getByRole("dialog", { name: "Approve invoice" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
