// Typed API client. Types come from the backend's OpenAPI schema (npm run gen:api).
import type { components } from "./api-schema";

type S = components["schemas"];
export type DocumentStatus = S["DocumentStatus"];
export type DocumentSummary = S["DocumentSummary"];
export type DocumentDetail = S["DocumentDetail"];
export type DocumentStatusView = S["DocumentStatusView"];
export type InvoiceResult = S["InvoiceResult"];
export type DocumentResult = S["DocumentResult"];
export type FieldView = S["FieldView"];
export type LineItemView = S["LineItemView"];
export type IssueView = S["IssueView"];
export type PageInfo = S["PageInfo"];
export type CorrectionView = S["CorrectionView"];
export type SearchHit = S["SearchHit"];
export type UploadResult = S["UploadResult"];
export type UploadResponse = S["UploadResponse"];
export type JobAccepted = S["JobAccepted"];
export type InvoicePatch = S["InvoicePatch"];
export type CrossCheckView = S["CrossCheckView"];
export type CrossCheckDetail = S["CrossCheckDetail"];
export type FindingView = S["FindingView"];
export type LedgerView = S["LedgerView"];
export type LedgerRowView = S["LedgerRowView"];
export type Paged<T> = { items: T[]; total: number; limit: number; offset: number };

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The backend's error format: {"error": {"code": ..., "message": ...}}. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } };
    if (body.error?.message) {
      return new ApiError(response.status, body.error.code ?? "error", body.error.message);
    }
  } catch {
    // not JSON: fall through to a generic message
  }
  return new ApiError(response.status, "http_error", `Request failed (HTTP ${response.status}).`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the API. Is the backend running?");
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

export type DocumentFilters = {
  status?: DocumentStatus | "";
  needs_review?: "" | "true" | "false";
  supplier?: string;
  date_from?: string;
  date_to?: string;
};

export function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  listDocuments: (
    filters: DocumentFilters & { limit: number; offset: number; order?: "newest" | "oldest" },
  ) => request<Paged<DocumentSummary>>(`/documents${query(filters)}`),
  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  getStatus: (id: string) => request<DocumentStatusView>(`/documents/${id}/status`),
  getResult: (id: string) => request<DocumentResult>(`/documents/${id}/result`),
  getPages: (id: string) => request<PageInfo[]>(`/documents/${id}/pages`),
  getCorrections: (invoiceId: string) =>
    request<CorrectionView[]>(`/invoices/${invoiceId}/corrections`),
  patchInvoice: (invoiceId: string, patch: InvoicePatch) =>
    request<InvoiceResult>(`/invoices/${invoiceId}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  approve: (invoiceId: string, reviewedBy: string, overrideReason?: string) =>
    request<InvoiceResult>(`/invoices/${invoiceId}/approve`, {
      method: "POST",
      body: JSON.stringify({ reviewed_by: reviewedBy, override_reason: overrideReason ?? null }),
    }),
  reprocess: (id: string, onlyFailed = false) =>
    request<JobAccepted>(`/documents/${id}/reprocess${onlyFailed ? "?only_failed=true" : ""}`, {
      method: "POST",
    }),
  retryFailed: () =>
    request<{ requeued: number }>(`/jobs/retry-failed`, { method: "POST" }),
  search: (
    params: DocumentFilters & { q: string; mode: string; limit: number; offset: number },
  ) => request<Paged<SearchHit>>(`/search${query(params)}`),
};

export const crossChecks = {
  create: (documentA: string, documentB: string) =>
    request<CrossCheckView>(`/cross-checks`, {
      method: "POST",
      body: JSON.stringify({ document_a_id: documentA, document_b_id: documentB }),
    }),
  list: (limit = 20) => request<Paged<CrossCheckView>>(`/cross-checks${query({ limit })}`),
  get: (id: string) => request<CrossCheckDetail>(`/cross-checks/${id}`),
};

export const getLedger = (id: string, limit = 500) =>
  request<LedgerView>(`/documents/${id}/ledger${query({ limit })}`);

export const pageImageUrl = (id: string, page: number) =>
  `${API_URL}/documents/${id}/pages/${page}/image`;
export const fileUrl = (id: string) => `${API_URL}/documents/${id}/file`;

/** Documents whose processing is not finished yet (used to decide when to poll). */
export function isActive(doc: { status: DocumentStatus; job?: { status: string } | null }) {
  return (
    doc.status === "uploaded" ||
    doc.status === "processing" ||
    doc.job?.status === "queued" ||
    doc.job?.status === "running"
  );
}
