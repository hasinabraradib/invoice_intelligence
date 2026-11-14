import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { DocumentFilters, DocumentSummary } from "@/lib/api";
import { Filters } from "@/components/Filters";
import { Select } from "@/components/controls/Select";
import { DocumentPicker } from "@/components/controls/DocumentPicker";
import { DisabledHint, Switch } from "@/components/controls/misc";
import { DateRange } from "@/components/controls/DateRange";
import { PRESETS, fromIso, monthGrid, rangeLabel, toIso } from "@/components/controls/dates";

describe("date helpers", () => {
  it("round-trips ISO dates in local time and rejects impossible ones", () => {
    expect(toIso(fromIso("2026-03-15")!)).toBe("2026-03-15");
    expect(fromIso("2026-02-31")).toBeNull();
    expect(fromIso("")).toBeNull();
  });

  it("builds a six-week grid starting on Monday", () => {
    const grid = monthGrid(new Date(2026, 2, 1)); // March 2026 starts on a Sunday
    expect(grid).toHaveLength(42);
    expect(toIso(grid[0])).toBe("2026-02-23");
    expect(grid[0].getDay()).toBe(1);
  });

  it("labels ranges and computes presets", () => {
    expect(rangeLabel({})).toBe("Any date");
    expect(rangeLabel({ from: "2026-03-01" })).toMatch(/^From /);
    expect(rangeLabel({ to: "2026-03-31" })).toMatch(/^Until /);
    const lastMonth = PRESETS.find((p) => p.key === "last-month")!.range(new Date(2026, 0, 20));
    expect(lastMonth).toEqual({ from: "2025-12-01", to: "2025-12-31" });
  });
});

describe("Select", () => {
  it("is labelled, opens a themed menu and maps the empty option", async () => {
    const onChange = vi.fn();
    render(
      <>
        <label htmlFor="s">Status</label>
        <Select id="s" value="" onChange={onChange} options={[{ value: "", label: "Any status" }, { value: "failed", label: "Failed" }]} />
      </>,
    );
    const trigger = screen.getByLabelText("Status");
    expect(trigger).toHaveTextContent("Any status");
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("option", { name: "Failed" }));
    expect(onChange).toHaveBeenLastCalledWith("failed");
  });
});

function doc(id: string, filename: string, supplier: string | null, invoices: number): DocumentSummary {
  return {
    id, filename, status: "processed", mime_type: "application/pdf", size_bytes: 1, page_count: 1,
    created_at: "2026-03-15T10:00:00Z", invoice_count: invoices, needs_review: false,
    invoice: supplier ? { supplier_name: supplier } : null,
  } as DocumentSummary;
}

describe("DocumentPicker", () => {
  const docs = [doc("a", "acme.pdf", "ACME Ltd", 1), doc("b", "ledger.xlsx", null, 0), doc("c", "globex.pdf", "Globex", 3)];

  it("filters by filename or supplier and picks with the keyboard", async () => {
    const onChange = vi.fn();
    render(<DocumentPicker id="p" value="" onChange={onChange} documents={docs} prompt="Invoice PDF" />);
    await userEvent.click(screen.getByRole("button", { name: /Invoice PDF/ }));
    const box = screen.getByRole("combobox");
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByRole("option", { name: /globex\.pdf.*Globex · 3 invoices/ })).toBeInTheDocument();
    await userEvent.type(box, "globex");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    await userEvent.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("c");
  });

  it("leaves out the document picked on the other side", async () => {
    render(<DocumentPicker id="p" value="" onChange={() => {}} documents={docs} prompt="Ledger" exclude="a" />);
    await userEvent.click(screen.getByRole("button", { name: /Ledger/ }));
    expect(screen.queryByRole("option", { name: /acme\.pdf/ })).toBeNull();
    expect(screen.getByRole("option", { name: /ledger\.xlsx.*no invoices/ })).toBeInTheDocument();
  });
});

describe("Filters toolbar", () => {
  function Harness({ onChange }: { onChange: (f: DocumentFilters) => void }) {
    const [value, setValue] = useState<DocumentFilters>({});
    return <Filters idPrefix="t" value={value} onChange={(f) => { setValue(f); onChange(f); }} />;
  }

  it("starts empty, shows removable chips and clears all", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    expect(screen.getByRole("button", { name: /Invoice date: Any date/ })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Active filters" })).toBeNull();

    await userEvent.type(screen.getByLabelText("Supplier"), "acme");
    await userEvent.click(screen.getByLabelText("Status"));
    await userEvent.click(screen.getByRole("option", { name: "Failed" }));
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getByRole("button", { name: /Status: Failed/ })).toBeInTheDocument();

    await userEvent.click(within(chips).getByRole("button", { name: /Supplier: acme/ }));
    expect(onChange).toHaveBeenLastCalledWith({ status: "failed", supplier: undefined });
    await userEvent.click(within(chips).getByRole("button", { name: "Clear all" }));
    expect(onChange).toHaveBeenLastCalledWith({});
  });
});

describe("DateRange", () => {
  it("applies a preset and a picked range", async () => {
    const onChange = vi.fn();
    render(<DateRange value={{}} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Invoice date: Any date/ }));
    await userEvent.click(screen.getByRole("button", { name: "This year" }));
    const year = new Date().getFullYear();
    expect(onChange).toHaveBeenLastCalledWith({ from: `${year}-01-01`, to: `${year}-12-31` });
  });

  it("orders the two clicked dates", async () => {
    const onChange = vi.fn();
    render(<DateRange value={{ from: "2026-03-20", to: "2026-03-20" }} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Invoice date:/ }));
    const grid = screen.getByRole("grid");
    await userEvent.click(within(grid).getByRole("button", { name: /20 March 2026|March 20, 2026/ }));
    await userEvent.click(within(grid).getByRole("button", { name: /10 March 2026|March 10, 2026/ }));
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onChange).toHaveBeenLastCalledWith({ from: "2026-03-10", to: "2026-03-20" });
  });
});

describe("DisabledHint and Switch", () => {
  it("explains why a button is disabled, and keeps the button when it is enabled", () => {
    const { rerender } = render(
      <DisabledHint disabled reason="Pick two documents to compare">
        <button type="button" disabled>Run</button>
      </DisabledHint>,
    );
    const button = screen.getByRole("button", { name: "Run" });
    expect(screen.getByRole("tooltip")).toHaveTextContent("Pick two documents to compare");
    rerender(
      <DisabledHint disabled={false} reason="Pick two documents to compare">
        <button type="button">Run</button>
      </DisabledHint>,
    );
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(screen.getByRole("button", { name: "Run" })).toBe(button); // not remounted
  });

  it("toggles as a switch", async () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Show matched" />);
    await userEvent.click(screen.getByRole("switch", { name: "Show matched" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
