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
export type FeedbackReason = "wrong_number" | "wrong_scope" | "wrong_source" | "missing_information" | "other";
export type FeedbackState = {
  feedback_id: string; turn_seq: number; rating: "up" | "down"; reasons: FeedbackReason[]; correction: string | null;
  status: "recorded" | "revising" | "revised" | "validated";
};
export type ConversationDetail = {
  conversation_id: string; turns: Turn[]; investigations: { request_id: string; turn_seq: number }[]; feedback?: FeedbackState[];
};
export type LearnedItem = {
  item_id: string; kind: "correction" | "approach"; scope: "personal" | "shared"; statement: string; question: string;
  use_count: number; created_at: string; last_used_at: string | null; expired: boolean;
};
export type Preferences = {
  default_fiscal_years: [number, number] | null; amount_unit: "usd" | "usd_millions" | null;
  answer_length: "brief" | "detailed" | null; table_lens: boolean | null;
};
export type Memory = { preferences: Preferences; items: LearnedItem[] };
export type Review = {
  review_id: string; kind: "share_correction" | "share_approach" | "eval_case"; submitted_by: string; status: string;
  question: string; statement: string; details: { steps?: string[]; tables?: string[]; filters?: string[] };
  answer_excerpt: string | null; reasons: FeedbackReason[]; created_at: string; note: string | null; expected_terms: string[];
};
export type LessonStatus = "proposed" | "approved" | "rejected";
export type LessonCard = {
  card_id: string; status: LessonStatus; headline: string; text: string; project_id: string | null; country: string | null;
  sector: string | null; doc_type: "ICR" | "ICRR" | "PAD"; source: string; citation: string; document_id: string;
  page_start: number | null; page_end: number | null; topics: string[]; stages: string[]; categories: string[];
  decided_by: string | null; note: string | null;
};
export type LessonQueue = { counts: Record<LessonStatus, number>; cards: LessonCard[] };
export type Me = { user_id: string; reviewer: boolean; feedback: boolean };
export type WatchedProject = { project_id: string; title: string | null; country: string | null; packages: number };
export type WatchWarning = {
  code: string; severity: "high" | "medium" | "low"; message: string; activity_id: string | null; reference: string | null;
  value: number | null; benchmark: Record<string, unknown> | null; citations: string[];
};
export type PackageView = {
  activity_id: string; reference: string; description: string; category: string | null; method: string | null; review_type: string | null;
  status: string; estimated_usd: number | null; contract_amount_usd: number | null; planned_signing: string | null; signed: string | null;
  slippage_days: number | null; contract_number: string | null; plan: string; page: number | null;
  stages: { name: string; planned: string | null; actual: string | null }[];
};
export type ProjectWatch = {
  project_id: string; title: string | null; country: string | null; as_of: string | null; warnings: WatchWarning[]; packages: PackageView[];
  summary: {
    packages: number; signed: number; open: number; cancelled: number; overdue: number; stale_plan_entries: number;
    median_slippage_days: number | null; warnings: Record<"high" | "medium" | "low", number>;
  };
  ratings: { document_id: string; date: string | null; indicator: string; previous: string | null; current: string }[];
};
export type Fact = { name: string; value: string; unit: string | null };
export type Evidence = {
  evidence_id: string; kind: string; citation: string; numeric_use: string; validation_status: string; warnings: string[];
  summary: string; document_id: string | null; pages: [number, number] | null; project_ids: string[]; source_url: string | null;
  facts: Fact[];
};
export type Investigation = {
  status: string; message: string; claims: { text: string; evidence_ids: string[]; section: string | null }[]; limitations: string[];
  error_codes: string[]; tasks: { task: string; worker: string; operation: string; state: string }[];
  pending_approval: { approval_id: string; kind: string; summary: string; expires_at: string } | null;
  evidence: Evidence[]; usage: Record<string, number | string | null>; applied_learning?: LearnedItem[];
};
export type Job = { job_id: string; kind: string; state: "running" | "succeeded" | "failed"; request_id: string | null; status: string | null; error: string | null };
export type PackageInput = {
  project_id: string; procurement_method: string; procurement_category: string; review_type: string;
  bid_window_days: number; tender_date: string;
};

export type TenderInput = PackageInput & { estimated_amount_usd: number | null; lots: number; draft_text: string };

export type ProjectProfile = {
  country_code: string; sector: string; description: string; approval_date: string; total_cost_usd: number | null;
};
export type PlannedPackage = {
  name: string; procurement_category: string; procurement_method: string | null; estimated_amount_usd: number | null;
  review_type: string; bid_window_days: number; tender_date: string;
};
export type PlanInput = { profile: ProjectProfile; packages: PlannedPackage[] };

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
  sendMessage: (id: string, text: string, execute: boolean, tableLens = false) =>
    call<Job>(`conversations/${id}/messages`, json("POST", { text, execute, table_lens: tableLens, client_message_id: clientId() })),
  assessPackage: (id: string, input: PackageInput) =>
    call<Job>(`conversations/${id}/packages`, json("POST", { ...input, client_message_id: clientId() })),
  checkTender: (id: string, input: TenderInput) =>
    call<Job>(`conversations/${id}/tenders`, json("POST", { ...input, client_message_id: clientId() })),
  draftText: (filename: string, contentBase64: string) =>
    call<{ text: string; characters: number }>("drafts/text", json("POST", { filename, content_base64: contentBase64 })),
  planProject: (id: string, input: PlanInput) =>
    call<Job>(`conversations/${id}/plans`, json("POST", { ...input, client_message_id: clientId() })),
  job: (id: string) => call<Job>(`jobs/${id}`),
  cancelJob: (id: string) => call<Job>(`jobs/${id}/cancel`, { method: "POST" }),
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
  me: () => call<Me>("me"),
  giveFeedback: (conversationId: string, turnSeq: number, rating: "up" | "down", reasons: FeedbackReason[] = [], correction?: string) =>
    call<{ feedback: FeedbackState; job: Job | null }>(`conversations/${conversationId}/feedback`, json("POST", {
      turn_seq: turnSeq, rating, reasons, correction: correction || null, client_message_id: clientId(),
    })),
  memory: () => call<Memory>("learning"),
  savePreferences: (update: Partial<Preferences>) => call<Preferences>("learning/preferences", json("PUT", update)),
  removeItem: (id: string) => call<void>(`learning/items/${id}`, { method: "DELETE" }),
  projects: () => call<WatchedProject[]>("projects"),
  watch: (projectId: string) => call<ProjectWatch>(`projects/${projectId}/watch`),
  lessons: (status: LessonStatus = "proposed") => call<LessonQueue>(`lessons?status=${status}`),
  decideLesson: (id: string, approved: boolean, note?: string) =>
    call<LessonCard>(`lessons/${id}`, json("POST", { approved, note: note || null })),
  reviews: (status = "pending") => call<Review[]>(`reviews?status=${status}`),
  decideReview: (id: string, approved: boolean, note?: string, expectedTerms: string[] = []) =>
    call<Review>(`reviews/${id}`, json("POST", { approved, note: note || null, expected_terms: expectedTerms })),
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
