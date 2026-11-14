"use client";
// A searchable document picker (combobox pattern): type to filter by filename or supplier,
// arrows to move, Enter to pick, Escape to close. Each row shows the file-type icon,
// filename, supplier, invoice count and upload date.
import * as Popover from "@radix-ui/react-popover";
import { useId, useMemo, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import type { DocumentSummary } from "@/lib/api";
import { FileTypeIcon } from "@/components/ui";

function detail(d: DocumentSummary): string {
  const kind = d.invoice_count > 1 ? `${d.invoice_count} invoices` : d.invoice_count === 1 ? "1 invoice" : "ledger, no invoices";
  const date = new Date(d.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  return [d.invoice?.supplier_name, kind, date].filter(Boolean).join(" · ");
}

type Props = {
  id: string;
  value: string;
  onChange: (id: string) => void;
  documents: DocumentSummary[];
  /** Says what to pick, e.g. "Invoice PDF". */
  prompt: string;
  exclude?: string;
};

export function DocumentPicker({ id, value, onChange, documents, prompt, exclude }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const selected = documents.find((d) => d.id === value);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return documents.filter(
      (d) => d.id !== exclude && (!q || d.filename.toLowerCase().includes(q) || (d.invoice?.supplier_name ?? "").toLowerCase().includes(q)),
    );
  }, [documents, query, exclude]);

  const choose = (d: DocumentSummary) => {
    onChange(d.id);
    setOpen(false);
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setQuery("");
          setActive(0);
        }
      }}
    >
      <Popover.Trigger
        id={id}
        aria-haspopup="listbox"
        className="input flex h-auto min-h-14 items-center gap-3 py-2 text-left data-[state=open]:border-ink"
      >
        {selected ? (
          <>
            <FileTypeIcon mime={selected.mime_type} filename={selected.filename} size="sm" />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-ink">{selected.filename}</span>
              <span className="block truncate text-xs text-muted">{detail(selected)}</span>
            </span>
          </>
        ) : (
          <span className="flex-1 text-muted">{prompt}</span>
        )}
        <ChevronDown className="size-4 shrink-0 text-muted" aria-hidden="true" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className="menu w-[var(--radix-popover-trigger-width)] min-w-72 animate-fade-in p-0"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            (e.currentTarget as HTMLElement).querySelector("input")?.focus();
          }}
        >
          <div className="flex items-center gap-2 border-b border-line px-3">
            <Search className="size-4 shrink-0 text-muted" aria-hidden="true" />
            <input
              role="combobox"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={matches[active] ? `${listId}-${matches[active].id}` : undefined}
              aria-label="Filter documents by filename or supplier"
              className="h-11 w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              placeholder="Filename or supplier"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, matches.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter" && matches[active]) {
                  e.preventDefault();
                  choose(matches[active]);
                }
              }}
            />
          </div>
          <ul id={listId} role="listbox" aria-label={prompt} className="max-h-72 overflow-y-auto p-1">
            {matches.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-muted">No document matches “{query}”.</li>
            ) : (
              matches.map((d, i) => (
                <li
                  key={d.id}
                  id={`${listId}-${d.id}`}
                  role="option"
                  aria-selected={d.id === value}
                  data-highlighted={i === active ? "" : undefined}
                  className="menu-item"
                  onMouseEnter={() => setActive(i)}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(d)}
                >
                  <FileTypeIcon mime={d.mime_type} filename={d.filename} size="sm" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{d.filename}</span>
                    <span className="num block truncate text-xs text-muted">{detail(d)}</span>
                  </span>
                  {d.id === value ? <Check className="size-4 shrink-0" aria-hidden="true" /> : null}
                </li>
              ))
            )}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
