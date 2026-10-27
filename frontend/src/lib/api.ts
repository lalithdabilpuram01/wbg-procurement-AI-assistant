// Browser-side client: every call goes through the same-origin proxy (/api/backend), which adds the session token.

export type Conversation = { conversation_id: string; title: string; updated_at: string; folder_id: string | null };
export type Folder = { folder_id: string; name: string; created_at: string };
export type Bookmark = {
  bookmark_id: string; thread_id: string; turn_seq: number; note: string; created_at: string;
  conversation_title: string; excerpt: string;
};
export type Turn = {
  seq: number; role: "user" | "assistant"; text: string; status: string | null; route: string | null;
  request_id: string | null; created_at: string;
};
export type ConversationDetail = { conversation_id: string; turns: Turn[]; investigations: { request_id: string; turn_seq: number }[] };
export type Fact = { name: string; value: string; unit: string | null };
export type Evidence = {
  evidence_id: string; kind: string; citation: string; numeric_use: string; validation_status: string; warnings: string[];
  summary: string; document_id: string | null; pages: [number, number] | null; project_ids: string[]; source_url: string | null;
  facts: Fact[];
};
export type Investigation = {
  status: string; message: string; claims: { text: string; evidence_ids: string[] }[]; limitations: string[];
  error_codes: string[]; tasks: { task: string; worker: string; operation: string; state: string }[];
  pending_approval: { approval_id: string; kind: string; summary: string; expires_at: string } | null;
  evidence: Evidence[]; usage: Record<string, number | string | null>;
};
export type Job = { job_id: string; kind: string; state: "running" | "succeeded" | "failed"; request_id: string | null; status: string | null; error: string | null };
export type PackageInput = {
  project_id: string; procurement_method: string; procurement_category: string; review_type: string;
  bid_window_days: number; tender_date: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, { ...init, cache: "no-store" });
  if (response.status === 401 && typeof window !== "undefined") window.location.assign("/login");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof body.detail === "string" ? body.detail : "The request failed.");
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const clientId = () => `ui-${crypto.randomUUID()}`;

export const api = {
  conversations: () => call<Conversation[]>("conversations"),
  conversation: (id: string) => call<ConversationDetail>(`conversations/${id}`),
  createConversation: (title?: string) => call<{ conversation_id: string; title: string }>("conversations", json("POST", { title })),
  renameConversation: (id: string, title: string) => call<void>(`conversations/${id}`, json("PATCH", { title })),
  fileConversation: (id: string, folderId: string | null) => call<void>(`conversations/${id}/folder`, json("PUT", { folder_id: folderId })),
  sendMessage: (id: string, text: string, execute: boolean) =>
    call<Job>(`conversations/${id}/messages`, json("POST", { text, execute, client_message_id: clientId() })),
  assessPackage: (id: string, input: PackageInput) =>
    call<Job>(`conversations/${id}/packages`, json("POST", { ...input, client_message_id: clientId() })),
  job: (id: string) => call<Job>(`jobs/${id}`),
  investigation: (conversationId: string, requestId: string) =>
    call<Investigation>(`conversations/${conversationId}/investigations/${requestId}`),
  decide: (conversationId: string, requestId: string, approvalId: string, approved: boolean) =>
    call<Job>(`conversations/${conversationId}/investigations/${requestId}/decision`,
      json("POST", { approval_id: approvalId, approved, client_message_id: clientId() })),
  folders: () => call<Folder[]>("folders"),
  createFolder: (name: string) => call<Folder>("folders", json("POST", { name })),
  renameFolder: (id: string, name: string) => call<Folder>(`folders/${id}`, json("PATCH", { name })),
  deleteFolder: (id: string) => call<void>(`folders/${id}`, { method: "DELETE" }),
  bookmarks: () => call<Bookmark[]>("bookmarks"),
  addBookmark: (conversationId: string, turnSeq: number, note = "") =>
    call<Bookmark>("bookmarks", json("POST", { conversation_id: conversationId, turn_seq: turnSeq, note })),
  deleteBookmark: (id: string) => call<void>(`bookmarks/${id}`, { method: "DELETE" }),
};

/** Polls a background job until it finishes (the API runs every request as a job). */
export async function waitForJob(job: Job, onTick?: (job: Job) => void, timeoutMs = 300_000): Promise<Job> {
  const started = Date.now();
  let current = job;
  while (current.state === "running" && Date.now() - started < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    current = await api.job(current.job_id);
    onTick?.(current);
  }
  return current;
}
