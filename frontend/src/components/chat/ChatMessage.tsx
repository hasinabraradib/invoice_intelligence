"use client";
import Link from "next/link";
import { AlertTriangle, Check, ChevronRight, FileText, Paperclip, Sparkles, Wrench } from "lucide-react";
import { citationHref, type ActionView, type Citation, type ToolUse } from "@/lib/assistant";
import { ActionCard } from "./ActionCard";
import { Markdown } from "./Markdown";

export type UIMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  tools: ToolUse[];
  actions: ActionView[];
  attachments: { document_id: string; filename: string }[];
  streaming?: boolean;
  stopped?: boolean;
  error?: string;
};

export function ChatMessage({ message, onAction }: { message: UIMessage; onAction: (a: ActionView) => void }) {
  if (message.role === "user") {
    return (
      <div className="flex animate-fade-up justify-end">
        <div className="max-w-[85%] rounded-3xl rounded-br-lg bg-accent px-4 py-2.5 text-[15px] leading-relaxed text-on-accent shadow-soft">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          {message.attachments.length > 0 ? (
            <p className="mt-1.5 flex items-center gap-1.5 text-xs opacity-85">
              <Paperclip className="size-3.5" aria-hidden="true" />
              {message.attachments.map((a) => a.filename).join(", ")}
            </p>
          ) : null}
        </div>
      </div>
    );
  }
  const names = [...new Set(message.tools.map((t) => t.name))];
  return (
    <div className="flex animate-fade-up gap-3">
      <span aria-hidden="true" className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border border-accent-line bg-accent-soft text-accent-ink">
        <Sparkles className="size-3.5" strokeWidth={2} />
      </span>
      <div className="min-w-0 max-w-[92%] flex-1 space-y-3 text-[15px] leading-relaxed text-ink">
        {names.length > 0 ? <ToolTrace tools={message.tools} names={names} streaming={message.streaming} /> : null}
        {message.content ? (
          <div className={message.streaming ? "streaming" : undefined}>
            <Markdown text={message.content} />
            {message.streaming ? (
              <span aria-hidden="true" className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-caret bg-accent align-baseline" />
            ) : null}
          </div>
        ) : message.streaming ? (
          <p className="flex items-center gap-2 text-muted" role="status">
            <span className="flex gap-1" aria-hidden="true">
              <span className="size-1.5 animate-pulse rounded-full bg-faint" />
              <span className="size-1.5 animate-pulse rounded-full bg-faint [animation-delay:150ms]" />
              <span className="size-1.5 animate-pulse rounded-full bg-faint [animation-delay:300ms]" />
            </span>
            {names.length ? "Looking it up…" : "Thinking…"}
          </p>
        ) : null}
        {message.stopped ? <p className="text-xs text-muted">(stopped)</p> : null}
        {message.error ? (
          <p className="flex items-center gap-1.5 text-sm text-danger">
            <AlertTriangle className="size-4" aria-hidden="true" />
            {message.error}
          </p>
        ) : null}
        {message.actions.map((action) => (
          <ActionCard key={action.id} action={action} onChange={onAction} />
        ))}
        {message.citations.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5 pt-0.5" aria-label="Sources">
            {message.citations.map((c, i) => (
              <li key={i}>
                <Link
                  href={citationHref(c)}
                  className="focus-ring chip hover:border-line-strong hover:bg-surface hover:text-ink"
                >
                  <FileText className="size-3.5 text-faint" aria-hidden="true" />
                  {c.filename}
                  {c.invoice_number ? <span className="text-muted">· {c.invoice_number}</span> : null}
                  {c.page ? <span className="text-muted">· p. {c.page}</span> : null}
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

function ToolTrace({ tools, names, streaming }: { tools: ToolUse[]; names: string[]; streaming?: boolean }) {
  return (
    <details className="group text-xs text-muted">
      <summary className="focus-ring inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-1 hover:text-ink [&::-webkit-details-marker]:hidden">
        <Wrench className="size-3.5" aria-hidden="true" />
        Used {names.length === 1 ? names[0] : `${names.length} tools`}
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
      </summary>
      <ol className="mt-2 space-y-1.5 border-l border-line pl-3">
        {tools.map((t, i) => (
          <li key={i} className="flex items-start gap-2">
            {t.ok === false ? (
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" aria-label="failed" />
            ) : t.ok ? (
              <Check className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="done" />
            ) : (
              <span className="mt-1 size-2 shrink-0 animate-pulse rounded-full bg-info-dot" aria-label="running" />
            )}
            <span>
              <code className="font-mono text-ink-2">{t.name}</code> {t.summary ?? (streaming ? "…" : "")}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}
