"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { assistant, greeting, suggestions, type ActionView, type Citation, type ToolUse } from "@/lib/assistant";
import { readSse } from "@/lib/sse";
import { uploadFile } from "@/lib/upload";
import { ACCEPTED_EXTENSIONS, checkUploadFile } from "@/lib/validation";
import { percent } from "@/lib/format";
import { ChatMessage, type UIMessage } from "@/components/chat/ChatMessage";
import { SkeletonRows } from "@/components/Feedback";
import { errorMessage, useToast } from "@/components/Toast";
import { ArrowUp, ArrowUpRight, ChevronDown, History, Paperclip, Plus, Sparkles, Square, Upload } from "lucide-react";

type Attachment = {
  key: string; name: string; progress: number; documentId?: string; status?: string;
  stage?: string | null; error?: string; sent?: boolean; followedUp?: boolean;
};
const FINAL = new Set(["processed", "needs_review", "failed", "partially_failed"]);

export default function AssistantPage() {
  return (
    <Suspense fallback={<SkeletonRows />}>
      <Assistant />
    </Suspense>
  );
}

function Assistant() {
  const params = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const queryClient = useQueryClient();
  const conversationId = params.get("c");
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [text, setText] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const stats = useQuery({ queryKey: ["stats"], queryFn: assistant.stats, refetchInterval: 15_000 });
  const history = useQuery({ queryKey: ["conversations"], queryFn: assistant.conversations });

  // Load a conversation from the URL (?c=...).
  useEffect(() => {
    if (!conversationId || streaming) return;
    let cancelled = false;
    assistant.conversation(conversationId).then((detail) => {
      if (cancelled) return;
      setMessages(detail.messages.map((m) => ({
        key: m.id, role: m.role, content: m.content, citations: (m.citations ?? []) as Citation[],
        tools: (m.tools_used ?? []) as ToolUse[], actions: m.actions ?? [],
        attachments: (m.attachments ?? []) as UIMessage["attachments"],
      })));
    }).catch((error) => toast.error(errorMessage(error)));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload only when the conversation changes
  }, [conversationId]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages]);

  const patchLast = (change: (m: UIMessage) => UIMessage) =>
    setMessages((all) => all.map((m, i) => (i === all.length - 1 ? change(m) : m)));

  const send = useCallback(async (content: string, files: Attachment[] = []) => {
    if (!content.trim() || streaming) return;
    let id = conversationId;
    try {
      if (!id) {
        id = (await assistant.create()).id;
        router.replace(`/?c=${id}`);
      }
    } catch (error) {
      toast.error(errorMessage(error));
      return;
    }
    const sentFiles = files.filter((f) => f.documentId).map((f) => ({ document_id: f.documentId as string, filename: f.name }));
    setMessages((all) => [...all,
      { key: `u${Date.now()}`, role: "user", content, citations: [], tools: [], actions: [], attachments: sentFiles },
      { key: `a${Date.now()}`, role: "assistant", content: "", citations: [], tools: [], actions: [], attachments: [], streaming: true }]);
    setStreaming(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const body = await assistant.send(id, content, sentFiles, controller.signal);
      for await (const { event, data } of readSse(body)) {
        const d = data as Record<string, unknown>;
        if (event === "token") patchLast((m) => ({ ...m, content: m.content + String(d.text) }));
        else if (event === "tool_call") patchLast((m) => ({ ...m, tools: [...m.tools, { name: String(d.name), arguments: d.arguments }] }));
        else if (event === "tool_result") patchLast((m) => {
          const tools = [...m.tools];
          const index = tools.findLastIndex((t) => t.name === d.name && t.ok === undefined);
          if (index >= 0) tools[index] = { ...tools[index], ok: Boolean(d.ok), summary: String(d.summary) };
          return { ...m, tools };
        });
        else if (event === "pending_action") patchLast((m) => ({ ...m, actions: [...m.actions, d as unknown as ActionView] }));
        else if (event === "citations") patchLast((m) => ({ ...m, citations: data as Citation[] }));
        else if (event === "error") patchLast((m) => ({ ...m, error: String(d.message) }));
      }
    } catch (error) {
      if (controller.signal.aborted) patchLast((m) => ({ ...m, stopped: true }));
      else patchLast((m) => ({ ...m, error: errorMessage(error) }));
    } finally {
      patchLast((m) => ({ ...m, streaming: false }));
      setStreaming(false);
      abort.current = null;
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    }
  }, [conversationId, streaming, router, toast, queryClient]);

  // Attachments: upload through the normal path, then follow their processing.
  function attach(list: FileList | null) {
    for (const file of Array.from(list ?? [])) {
      const key = `${file.name}-${Date.now()}-${Math.random()}`;
      const problem = checkUploadFile(file);
      setAttachments((all) => [...all, { key, name: file.name, progress: 0, error: problem ?? undefined }]);
      if (problem) continue;
      uploadFile(file, (progress) => setAttachments((all) => all.map((a) => a.key === key ? { ...a, progress } : a)))
        .then((result) => setAttachments((all) => all.map((a) => a.key === key ? {
          ...a, progress: 1, documentId: result.document_id ?? undefined,
          error: result.error?.message, status: result.duplicate ? "duplicate" : "uploaded",
        } : a)))
        .catch((error: unknown) => setAttachments((all) => all.map((a) => a.key === key ? { ...a, error: errorMessage(error) } : a)));
    }
  }

  useEffect(() => {
    const active = attachments.filter((a) => a.documentId && !FINAL.has(a.status ?? "") && a.status !== "duplicate");
    if (active.length === 0) return;
    const timer = setInterval(async () => {
      for (const a of active) {
        try {
          const s = await api.getStatus(a.documentId as string);
          setAttachments((all) => all.map((x) => x.key === a.key ? { ...x, status: s.status, stage: s.stage } : x));
        } catch { /* keep polling */ }
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [attachments]);

  // When a file that was sent in the chat finishes, ask the assistant to summarise it.
  useEffect(() => {
    const finished = attachments.find((a) => a.sent && !a.followedUp && FINAL.has(a.status ?? ""));
    if (!finished || streaming) return;
    setAttachments((all) => all.map((a) => a.key === finished.key ? { ...a, followedUp: true } : a));
    void send(`${finished.name} has finished processing (${finished.status}). Please summarise what you found.`,
      [finished]);
  }, [attachments, streaming, send]);

  function submit() {
    const ready = attachments.filter((a) => a.documentId && !a.sent);
    const content = text.trim() || (ready.length ? `I uploaded ${ready.map((a) => a.name).join(", ")}. Tell me when it is processed and what you find.` : "");
    if (!content) return;
    setText("");
    setAttachments((all) => all.map((a) => ready.includes(a) ? { ...a, sent: true } : a));
    void send(content, ready);
  }

  const empty = messages.length === 0;
  const live = stats.data
    ? [
        stats.data.invoices_need_review ? { label: `${stats.data.invoices_need_review} to review`, href: "/review", tone: "text-warning" } : null,
        stats.data.jobs_running + stats.data.jobs_queued ? { label: `${stats.data.jobs_running + stats.data.jobs_queued} processing`, href: "/documents?status=processing", tone: "text-info" } : null,
        stats.data.documents_failed ? { label: `${stats.data.documents_failed} failed`, href: "/documents?status=failed", tone: "text-danger" } : null,
      ].filter((x): x is { label: string; href: string; tone: string } => x !== null)
    : [];
  return (
    <div className="grid gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
      <aside aria-label="Conversations" className="min-w-0">
        <details className="card p-3 lg:hidden">
          <summary className="focus-ring flex cursor-pointer list-none items-center justify-between rounded-lg px-1 text-sm font-medium text-ink [&::-webkit-details-marker]:hidden">
            <span className="flex items-center gap-2"><History className="size-4 text-muted" aria-hidden="true" /> Conversations</span>
            <ChevronDown className="size-4 text-muted" aria-hidden="true" />
          </summary>
          <div className="mt-3">
            <Link href="/" className="btn-secondary mb-2 w-full" onClick={() => setMessages([])}><Plus className="size-4" aria-hidden="true" /> New chat</Link>
            <HistoryList items={history.data?.items ?? []} current={conversationId} />
          </div>
        </details>
        <div className="sticky top-10 hidden lg:block">
          <Link href="/" className="btn-primary mb-4 w-full" onClick={() => setMessages([])}><Plus className="size-4" aria-hidden="true" /> New chat</Link>
          <p className="eyebrow mb-2 px-2">Recent</p>
          <HistoryList items={history.data?.items ?? []} current={conversationId} />
        </div>
      </aside>

      <section aria-label="Chat" className="relative flex min-h-[calc(100vh-11rem)] min-w-0 flex-col md:min-h-[calc(100vh-6rem)]"
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); attach(e.dataTransfer.files); }}>
        {dragging ? (
          <div className="pointer-events-none absolute inset-0 z-20 grid animate-fade-in place-items-center rounded-card border-2 border-dashed border-accent bg-accent-soft/90 backdrop-blur-sm">
            <div className="text-center">
              <Upload className="mx-auto mb-2 size-6 text-accent-ink" aria-hidden="true" />
              <p className="font-medium text-ink">Drop invoices to upload</p>
              <p className="text-sm text-muted">PDF, images, XLSX or CSV</p>
            </div>
          </div>
        ) : null}
        <div className="mx-auto w-full max-w-3xl flex-1 space-y-7 pb-6" aria-live="polite">
          {empty ? (
            <div className="flex min-h-[50vh] flex-col items-center justify-center py-10 text-center">
              <span aria-hidden="true" className="mb-5 grid size-12 animate-fade-up place-items-center rounded-2xl border border-accent-line bg-accent-soft text-accent-ink shadow-soft">
                <Sparkles className="size-5" strokeWidth={1.75} />
              </span>
              <h1 className="max-w-xl animate-fade-up text-balance text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                {greeting(stats.data, new Date().getHours())}
              </h1>
              <p className="mt-3 max-w-md animate-fade-up text-sm text-muted">
                Ask about your invoices, upload new ones, or ask me to correct, approve or cross-check something. I always ask before changing anything.
              </p>
              {live.length > 0 ? (
                <ul className="mt-5 flex flex-wrap justify-center gap-2" aria-label="Right now">
                  {live.map((item) => (
                    <li key={item.label}>
                      <Link href={item.href} className={`focus-ring chip hover:border-line-strong ${item.tone}`}>{item.label}</Link>
                    </li>
                  ))}
                </ul>
              ) : null}
              <ul className="mt-8 grid w-full max-w-xl gap-2 sm:grid-cols-2" aria-label="Suggestions">
                {suggestions(stats.data).map((s) => (
                  <li key={s}>
                    <button type="button" className="focus-ring card card-hover flex w-full items-center justify-between gap-3 p-3.5 text-left text-sm text-ink-2"
                      onClick={() => void send(s)}>
                      {s}
                      <ArrowUpRight className="size-4 shrink-0 text-faint" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : messages.map((m) => (
            <ChatMessage key={m.key} message={m}
              onAction={(updated) => setMessages((all) => all.map((x) => ({ ...x, actions: x.actions.map((a) => a.id === updated.id ? updated : a) })))} />
          ))}
          <div ref={bottom} />
        </div>

        <div className="sticky bottom-20 z-10 mx-auto w-full max-w-3xl bg-gradient-to-t from-bg via-bg to-transparent pt-4 md:bottom-4">
          {attachments.length > 0 ? (
            <ul className="mb-2 flex flex-wrap gap-2" aria-label="Attached files">
              {attachments.map((a) => (
                <li key={a.key} className="chip bg-surface">
                  <Paperclip className="size-3.5 text-faint" aria-hidden="true" />
                  <span className="max-w-40 truncate">{a.name}</span>
                  <span className="text-muted">·</span>
                  {a.error ? <span className="text-danger">{a.error}</span>
                    : a.progress < 1 ? <span className="num text-muted">uploading {percent(a.progress)}</span>
                    : a.documentId ? <Link className="btn-link text-xs" href={`/documents/${a.documentId}`}>{a.status === "duplicate" ? "already uploaded" : `${a.status ?? "uploaded"}${a.stage && !FINAL.has(a.status ?? "") ? ` · ${a.stage}` : ""}`}</Link>
                    : "…"}
                </li>
              ))}
            </ul>
          ) : null}
          <form className="flex items-end gap-2 rounded-[1.75rem] border border-line bg-surface p-2 shadow-lift transition-colors focus-within:border-accent-line" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <input ref={fileInput} id="chat-file" type="file" multiple accept={ACCEPTED_EXTENSIONS.join(",")} className="sr-only"
              onChange={(e) => { attach(e.target.files); e.target.value = ""; }} />
            <button type="button" className="btn-ghost size-10 shrink-0 rounded-full p-0" onClick={() => fileInput.current?.click()} aria-label="Attach invoices">
              <Paperclip className="size-[18px]" aria-hidden="true" />
            </button>
            <label htmlFor="chat-input" className="sr-only">Message</label>
            <textarea id="chat-input" rows={1} className="max-h-40 min-h-10 flex-1 resize-none bg-transparent px-1 py-2 text-[15px] text-ink outline-none placeholder:text-faint"
              placeholder="Ask about your invoices…"
              value={text} onChange={(e) => setText(e.target.value)} maxLength={4000}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
            {streaming ? (
              <button type="button" className="btn-secondary size-10 shrink-0 rounded-full p-0" onClick={() => abort.current?.abort()} aria-label="Stop">
                <Square className="size-3.5 fill-current" aria-hidden="true" />
              </button>
            ) : (
              <button type="submit" className="btn-primary size-10 shrink-0 rounded-full p-0" aria-label="Send" disabled={!text.trim() && !attachments.some((a) => a.documentId && !a.sent)}>
                <ArrowUp className="size-[18px]" aria-hidden="true" />
              </button>
            )}
          </form>
          <p className="mt-2 text-center text-[11px] text-faint">Answers cite their sources. Changes need your confirmation.</p>
        </div>
      </section>
    </div>
  );
}

function HistoryList({ items, current }: { items: { id: string; title?: string | null }[]; current: string | null }) {
  if (items.length === 0) return <p className="px-2 text-xs text-muted">No conversations yet.</p>;
  return (
    <ul className="space-y-0.5">
      {items.map((c) => (
        <li key={c.id}>
          <Link href={`/?c=${c.id}`} aria-current={c.id === current ? "page" : undefined}
            className={`focus-ring block truncate rounded-xl px-2.5 py-2 text-sm transition-colors ${c.id === current ? "bg-surface font-medium text-ink shadow-soft" : "text-muted hover:bg-surface-2 hover:text-ink"}`}>
            {c.title ?? "Untitled"}
          </Link>
        </li>
      ))}
    </ul>
  );
}
