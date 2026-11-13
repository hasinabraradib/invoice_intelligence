"use client";
import Link from "next/link";
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
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-slate-900 px-3 py-2 text-sm text-white">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          {message.attachments.length > 0 ? (
            <p className="mt-1 text-xs opacity-80">📎 {message.attachments.map((a) => a.filename).join(", ")}</p>
          ) : null}
        </div>
      </div>
    );
  }
  const names = [...new Set(message.tools.map((t) => t.name))];
  return (
    <div className="max-w-[95%] space-y-2 text-sm text-slate-900">
      {names.length > 0 ? (
        <details className="text-xs text-slate-600">
          <summary className="focus-ring cursor-pointer rounded">Used: {names.join(", ")}</summary>
          <ul className="mt-1 space-y-0.5 pl-4">
            {message.tools.map((t, i) => (
              <li key={i}>
                <code>{t.name}</code> {t.ok === false ? "⚠ " : ""}{t.summary ?? (message.streaming ? "…" : "")}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {message.content ? <Markdown text={message.content} /> : message.streaming ? (
        <p className="text-slate-500" role="status">{names.length ? "Looking it up…" : "Thinking…"}</p>
      ) : null}
      {message.stopped ? <p className="text-xs text-slate-500">(stopped)</p> : null}
      {message.error ? <p className="text-xs text-red-700">{message.error}</p> : null}
      {message.actions.map((action) => <ActionCard key={action.id} action={action} onChange={onAction} />)}
      {message.citations.length > 0 ? (
        <ul className="flex flex-wrap gap-1" aria-label="Sources">
          {message.citations.map((c, i) => (
            <li key={i}>
              <Link href={citationHref(c)}
                className="focus-ring inline-flex rounded-full border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-700 hover:bg-slate-50">
                {c.filename}{c.invoice_number ? ` · ${c.invoice_number}` : ""}{c.page ? ` · p. ${c.page}` : ""}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
