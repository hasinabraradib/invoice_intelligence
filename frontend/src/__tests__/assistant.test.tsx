import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Markdown, safeHref } from "@/components/chat/Markdown";
import { ActionCard } from "@/components/chat/ActionCard";
import { ToastProvider } from "@/components/Toast";
import { citationHref, greeting, suggestions, type Stats } from "@/lib/assistant";
import { parseSseBlock, readSse } from "@/lib/sse";

const base: Stats = {
  documents: 12, documents_by_status: {}, invoices: 30, invoices_need_review: 9, jobs_running: 2,
  jobs_queued: 0, documents_failed: 0, spreadsheets: 1, cross_checks: 0,
};

describe("safe markdown", () => {
  it("renders bold, lists, code and safe links", () => {
    const { container } = render(<Markdown text={"Total is **1,320.00 BDT**:\n\n- one `x`\n- two\n\nSee [the invoice](/documents/abc?page=2)."} />);
    expect(container.querySelector("strong")?.textContent).toBe("1,320.00 BDT");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("code")?.textContent).toBe("x");
    expect(screen.getByRole("link", { name: "the invoice" })).toHaveAttribute("href", "/documents/abc?page=2");
  });

  it("never turns text into HTML", () => {
    const { container } = render(<Markdown text={'<script>alert(1)</script> <img src=x onerror="y"> [x](javascript:alert(1))'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();  // javascript: is not linked
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("only allows http(s) and in-app links", () => {
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("/documents/1")).toBe("/documents/1");
    expect(safeHref("//evil.example")).toBeNull();
    expect(safeHref("data:text/html,hi")).toBeNull();
  });
});

describe("server-sent events", () => {
  it("parses blocks", () => {
    expect(parseSseBlock('event: token\ndata: {"text": "Hi"}')).toEqual({ event: "token", data: { text: "Hi" } });
    expect(parseSseBlock(": comment only")).toBeNull();
  });

  it("handles events split across network chunks", async () => {
    const chunks = ['event: tok', 'en\ndata: {"text": "a"}\n', '\nevent: done\ndata: {}\n\n'];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(new TextEncoder().encode(c));
        controller.close();
      },
    });
    const events = [];
    for await (const e of readSse(body)) events.push(e);
    expect(events).toEqual([{ event: "token", data: { text: "a" } }, { event: "done", data: {} }]);
  });
});

describe("greeting and suggestions", () => {
  it("greets with live numbers", () => {
    expect(greeting(base, 20)).toBe("Good evening. 9 invoices need review, 2 jobs running.");
    expect(greeting({ ...base, invoices_need_review: 1, jobs_running: 0 }, 9)).toBe("Good morning. 1 invoice needs review.");
    expect(greeting({ ...base, documents: 0 }, 14)).toMatch(/^Good afternoon\. Upload an invoice/);
  });

  it("suggests what fits the data, at most four", () => {
    expect(suggestions(base)).toEqual(["What needs my review?", "How much did we spend this month, per supplier?",
      "Cross-check my invoices against the ledger", "What is still processing?"]);
    expect(suggestions({ ...base, documents: 0 })).toEqual(["How do I upload invoices?", "What can you do?"]);
    expect(suggestions({ ...base, invoices_need_review: 0, jobs_running: 0, spreadsheets: 0 }))
      .toEqual(["How much did we spend this month, per supplier?", "Show my latest documents"]);
  });

  it("links citations to the review page at the page", () => {
    expect(citationHref({ document_id: "d", filename: "a.pdf", invoice_id: "i", invoice_number: "X",
      segment_index: 2, page: 5 })).toBe("/documents/d?invoice=2&page=5");
  });
});

describe("pending action card", () => {
  afterEach(() => vi.restoreAllMocks());

  it("changes nothing until Confirm, then calls the API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "a1", tool: "correct_field", summary: "s", status: "confirmed", expires_at: new Date(Date.now() + 6e5).toISOString() }), { status: 200 }));
    const onChange = vi.fn();
    const action = { id: "a1", tool: "correct_field", summary: "Change the total of INV-1 from 10.00 to 12.50.",
      status: "pending" as const, expires_at: new Date(Date.now() + 6e5).toISOString() };
    render(<ToastProvider><ActionCard action={action} onChange={onChange} /></ToastProvider>);

    expect(screen.getByText(/Nothing changes until you confirm/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/assistant\/actions\/a1\/confirm$/);
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "confirmed" }));
  });

  it("shows expired actions without buttons", () => {
    const action = { id: "a2", tool: "approve_invoice", summary: "Approve X.", status: "pending" as const,
      expires_at: new Date(Date.now() - 1000).toISOString() };
    render(<ToastProvider><ActionCard action={action} onChange={() => {}} /></ToastProvider>);
    expect(screen.getByText("Change expired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });
});
