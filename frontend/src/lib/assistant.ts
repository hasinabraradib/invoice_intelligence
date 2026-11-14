// Assistant API calls and the greeting/suggestion logic (pure, so it is easy to test).
import type { components } from "./api-schema";
import { API_URL, toApiError, ApiError, type Paged } from "./api";

type S = components["schemas"];
export type Stats = S["Stats"];
export type ActionView = S["ActionView"];
export type ConversationView = S["ConversationView"];
export type ConversationDetail = S["ConversationDetail"];
export type Citation = {
  document_id: string; filename: string; invoice_id: string | null;
  invoice_number: string | null; segment_index: number | null; page: number | null;
};
export type ToolUse = { name: string; ok?: boolean; summary?: string; arguments?: unknown };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { ...init, headers: { "Content-Type": "application/json" } });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the API. Is the backend running?");
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export const assistant = {
  stats: () => call<Stats>("/stats"),
  conversations: () => call<Paged<ConversationView>>("/assistant/conversations?limit=50"),
  conversation: (id: string) => call<ConversationDetail>(`/assistant/conversations/${id}`),
  create: () => call<ConversationView>("/assistant/conversations", { method: "POST", body: "{}" }),
  confirm: (id: string) => call<ActionView>(`/assistant/actions/${id}/confirm`, { method: "POST" }),
  cancel: (id: string) => call<ActionView>(`/assistant/actions/${id}/cancel`, { method: "POST" }),
  /** Returns the raw streaming response (read it with readSse). */
  async send(conversationId: string, content: string, attachments: { document_id: string; filename: string }[], signal: AbortSignal) {
    const response = await fetch(`${API_URL}/assistant/conversations/${conversationId}/messages`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, attachments }), signal,
    });
    if (!response.ok || !response.body) throw await toApiError(response);
    return response.body;
  },
};

export function salutation(hour: number): string {
  return hour < 5 ? "Good evening" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** One line under the greeting, from live counts, e.g. "9 invoices need your review and
 * 2 documents are processing." Never guesses: no stats, no sentence. */
export function liveSummary(stats: Stats | undefined): string | null {
  if (!stats) return null;
  if (stats.documents === 0) return "Nothing uploaded yet. Drop an invoice here to get started, or ask me anything.";
  const review = stats.invoices_need_review;
  const jobs = stats.jobs_running + stats.jobs_queued;
  const bits = [
    review
      ? `${plural(review, "invoice", "invoices")} ${review === 1 ? "needs" : "need"} your review`
      : "No invoices need your review",
  ];
  if (jobs) bits.push(`${plural(jobs, "document", "documents")} ${jobs === 1 ? "is" : "are"} processing`);
  if (stats.documents_failed) bits.push(`${stats.documents_failed} failed`);
  const last = bits.pop();
  return `${bits.length ? `${bits.join(", ")} and ${last}` : last}.`;
}

/** 3-4 suggestions that fit the current state of the data. */
export function suggestions(stats: Stats | undefined): string[] {
  if (!stats || stats.documents === 0) return ["How do I upload invoices?", "What can you do?"];
  const out: string[] = [];
  if (stats.invoices_need_review) out.push("What needs my review?");
  if (stats.invoices) out.push("How much did we spend this month, per supplier?");
  if (stats.documents_failed) out.push("Which documents failed, and why?");
  if (stats.spreadsheets && stats.invoices) out.push("Cross-check my invoices against the ledger");
  if (stats.jobs_running + stats.jobs_queued) out.push("What is still processing?");
  if (out.length < 3) out.push("Show my latest documents");
  return out.slice(0, 4);
}

export function citationHref(c: Citation): string {
  return `/documents/${c.document_id}?invoice=${c.segment_index ?? 0}&page=${c.page ?? 1}`;
}
