// Browser-side client: every call goes through the same-origin proxy (/api/backend), which adds the session token.

/** A chat in the sidebar list (GET /conversations); `folder_id` is null when it is not filed. */
export type Conversation = { conversation_id: string; title: string; updated_at: string; folder_id: string | null };
/** A user's folder for grouping chats. */
export type Folder = { folder_id: string; name: string; created_at: string };
/** A bookmarked answer: the conversation (`thread_id`), the turn number, an optional note and a text excerpt. */
export type Bookmark = {
  bookmark_id: string; thread_id: string; turn_seq: number; note: string; created_at: string;
  conversation_title: string; excerpt: string;
};
/** One message of a conversation. An assistant turn has the answer `status`, the `route` taken and the `request_id`
 *  of its investigation, when there is one. */
export type Turn = {
  seq: number; role: "user" | "assistant"; text: string; status: string | null; route: string | null;
  request_id: string | null; created_at: string;
};
/** Why an answer was marked wrong. */
export type FeedbackReason = "wrong_number" | "wrong_scope" | "wrong_source" | "missing_information" | "other";
/** The feedback on one answer and how far it has got (recorded, revising, revised, validated). */
export type FeedbackState = {
  feedback_id: string; turn_seq: number; rating: "up" | "down"; reasons: FeedbackReason[]; correction: string | null;
  status: "recorded" | "revising" | "revised" | "validated";
};
/** A conversation with its turns, the investigations behind its answers and the feedback on them. */
export type ConversationDetail = {
  conversation_id: string; turns: Turn[]; investigations: { request_id: string; turn_seq: number }[]; feedback?: FeedbackState[];
};
/** A saved correction or approach that can guide later answers, personal or shared with the team. */
export type LearnedItem = {
  item_id: string; kind: "correction" | "approach"; scope: "personal" | "shared"; statement: string; question: string;
  use_count: number; created_at: string; last_used_at: string | null; expired: boolean;
};
/** The user's answer preferences; null means "no preference". */
export type Preferences = {
  default_fiscal_years: [number, number] | null; amount_unit: "usd" | "usd_millions" | null;
  answer_length: "brief" | "detailed" | null; table_lens: boolean | null;
};
/** Everything the app has learned for this user (GET /learning). */
export type Memory = { preferences: Preferences; items: LearnedItem[] };
/** An item in the reviewer queue: a correction or approach to share, or an answer to keep as an evaluation case. */
export type Review = {
  review_id: string; kind: "share_correction" | "share_approach" | "eval_case"; submitted_by: string; status: string;
  question: string; statement: string; details: { steps?: string[]; tables?: string[]; filters?: string[] };
  answer_excerpt: string | null; reasons: FeedbackReason[]; created_at: string; note: string | null; expected_terms: string[];
};
/** Review state of a lesson card. */
export type LessonStatus = "proposed" | "approved" | "rejected";
/** A lesson taken from a project document (completion report, IEG review or appraisal), with its citation. */
export type LessonCard = {
  card_id: string; status: LessonStatus; headline: string; text: string; project_id: string | null; country: string | null;
  sector: string | null; doc_type: "ICR" | "ICRR" | "PAD"; source: string; citation: string; document_id: string;
  page_start: number | null; page_end: number | null; topics: string[]; stages: string[]; categories: string[];
  decided_by: string | null; note: string | null;
};
/** One page of the lesson review queue: the cards of one status and the counts of every status. */
export type LessonQueue = { counts: Record<LessonStatus, number>; cards: LessonCard[] };
/** The signed-in user (GET /me). `feedback` is true when the API has its learning store, which enables feedback. */
export type Me = { user_id: string; reviewer: boolean; feedback: boolean };
/** A KPI dashboard: where its data comes from and the links to the full Databricks dashboard. */
export type Dashboard = { key: string; title: string; source: string; embed_url: string; open_url: string };
/** One bar or column of a KPI chart; `count` is the number of awards behind a value, when given. */
export type KpiBar = { label: string | number; value: number; count?: number };
/** The KPIs of one dashboard, or why they are missing (the pipeline catalog is not built, or the warehouse failed). */
export type Kpis =
  | { status: "ok"; catalog: string; value_by_year: KpiBar[]; awards_by_method: KpiBar[]; top_countries: KpiBar[]; bid_windows: KpiBar[];
      overview: { total_projects: number; contract_awards: number; total_contract_award_value_usd: number; procurement_notices: number;
                  ibrd_loan_instruments: number; ida_credit_instruments: number; last_contract_signing_date: string;
                  contracts_source_as_of_date: string; contracts_gold_run_id: string } }
  | { status: "not_built"; catalog: string }
  | { status: "unavailable"; catalog: string; detail: string };
/** A project the Project watch view can show, with its number of planned packages. */
export type WatchedProject = { project_id: string; title: string | null; country: string | null; packages: number };
/** An early warning on a project or package, with the records it cites and, when used, its benchmark. */
export type WatchWarning = {
  code: string; severity: "high" | "medium" | "low"; message: string; activity_id: string | null; reference: string | null;
  value: number | null; benchmark: Record<string, unknown> | null; citations: string[];
};
/** One procurement-plan package in Project watch: plan data, matched contract, slippage and stage dates. */
export type PackageView = {
  activity_id: string; reference: string; description: string; category: string | null; method: string | null; review_type: string | null;
  status: string; estimated_usd: number | null; contract_amount_usd: number | null; planned_signing: string | null; signed: string | null;
  slippage_days: number | null; contract_number: string | null; plan: string; page: number | null;
  stages: { name: string; planned: string | null; actual: string | null }[];
};
/** The monitoring view of one project (GET /projects/{id}/watch). */
export type ProjectWatch = {
  project_id: string; title: string | null; country: string | null; as_of: string | null; warnings: WatchWarning[]; packages: PackageView[];
  summary: {
    packages: number; signed: number; open: number; cancelled: number; overdue: number; stale_plan_entries: number;
    median_slippage_days: number | null; warnings: Record<"high" | "medium" | "low", number>;
  };
  ratings: { document_id: string; date: string | null; indicator: string; previous: string | null; current: string }[];
};
/** A named value on an evidence item, such as a model estimate or a table cell. */
export type Fact = { name: string; value: string; unit: string | null };
/** One source behind an answer: what kind it is, its citation, summary, facts, warnings and validation status. */
export type Evidence = {
  evidence_id: string; kind: string; citation: string; numeric_use: string; validation_status: string; warnings: string[];
  summary: string; document_id: string | null; pages: [number, number] | null; project_ids: string[]; source_url: string | null;
  facts: Fact[];
};
/** The full result of one request: status, message, claims linked to evidence, limitations and the tasks that ran. */
export type Investigation = {
  status: string; message: string; claims: { text: string; evidence_ids: string[]; section: string | null }[]; limitations: string[];
  error_codes: string[]; tasks: { task: string; worker: string; operation: string; state: string }[];
  evidence: Evidence[]; usage: Record<string, number | string | null>; applied_learning?: LearnedItem[];
};
/** A background job on the API. Every request runs as one; the browser follows it by streaming or polling. */
export type Job = { job_id: string; kind: string; state: "running" | "succeeded" | "failed"; request_id: string | null; status: string | null; error: string | null };
/** The package fields shared by the assessment and tender-check forms. */
export type PackageInput = {
  project_id: string; procurement_method: string; procurement_category: string; review_type: string;
  bid_window_days: number; tender_date: string;
};
/** A package to assess: every model input comes from the user, so the project may have no ID yet (null). */
export type PackageAssessmentInput = Omit<PackageInput, "project_id"> & {
  project_id: string | null; region_name: string | null; country_code: string; sector: string; board_approval_date: string;
  income_group: string | null; prior_tender_notices: number;
};

/** A tender check request: the package, its estimate and lots, and the draft text. */
export type TenderInput = PackageInput & { estimated_amount_usd: number | null; lots: number; draft_text: string };

/** The new project described in the planning form. */
export type ProjectProfile = {
  country_code: string; sector: string; description: string; approval_date: string; total_cost_usd: number | null;
};
/** A package planned for the new project; a null method asks the API to suggest one from history. */
export type PlannedPackage = {
  name: string; procurement_category: string; procurement_method: string | null; estimated_amount_usd: number | null;
  review_type: string; bid_window_days: number; tender_date: string;
};
/** The planning request: the project profile and its planned packages. */
export type PlanInput = { profile: ProjectProfile; packages: PlannedPackage[] };

/** An error answer from the API, with its HTTP status and the API's message (`detail`). */
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Calls one API endpoint through the proxy and parses the JSON answer (undefined for 204 No Content). A 401 means
 *  the session ended, so the browser goes to the sign-in page; other errors are thrown as ApiError. */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, { ...init, cache: "no-store" });
  if (response.status === 401 && typeof window !== "undefined") window.location.assign("/login");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof body.detail === "string" ? body.detail : "The request failed.");
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** Request options for a JSON body. The proxy sets the Content-Type header itself. */
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
/** A unique id per request. crypto.randomUUID exists only on secure pages (https, localhost), so the app also works
 *  when opened at the network address (http://192.168.x.x:3000). */
const clientId = () => {
  if (typeof crypto.randomUUID === "function") return `ui-${crypto.randomUUID()}`;
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `ui-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
};

/** One typed function per API endpoint used by the app; each path is relative to /api/backend. Requests that start
 *  work send a new `client_message_id`; the API returns the existing job when it sees the same id twice. */
export const api = {
  conversations: () => call<Conversation[]>("conversations"),
  conversation: (id: string) => call<ConversationDetail>(`conversations/${id}`),
  createConversation: (title?: string) => call<{ conversation_id: string; title: string }>("conversations", json("POST", { title })),
  renameConversation: (id: string, title: string) => call<void>(`conversations/${id}`, json("PATCH", { title })),
  deleteConversation: (id: string) => call<void>(`conversations/${id}`, { method: "DELETE" }),
  fileConversation: (id: string, folderId: string | null) => call<void>(`conversations/${id}/folder`, json("PUT", { folder_id: folderId })),
  sendMessage: (id: string, text: string, execute: boolean, tableLens = false) =>
    call<Job>(`conversations/${id}/messages`, json("POST", { text, execute, table_lens: tableLens, client_message_id: clientId() })),
  assessPackage: (id: string, input: PackageAssessmentInput) =>
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
  folders: () => call<Folder[]>("folders"),
  createFolder: (name: string) => call<Folder>("folders", json("POST", { name })),
  renameFolder: (id: string, name: string) => call<Folder>(`folders/${id}`, json("PATCH", { name })),
  deleteFolder: (id: string) => call<void>(`folders/${id}`, { method: "DELETE" }),
  bookmarks: () => call<Bookmark[]>("bookmarks"),
  addBookmark: (conversationId: string, turnSeq: number, note = "") =>
    call<Bookmark>("bookmarks", json("POST", { conversation_id: conversationId, turn_seq: turnSeq, note })),
  deleteBookmark: (id: string) => call<void>(`bookmarks/${id}`, { method: "DELETE" }),
  me: () => call<Me>("me"),
  dashboards: () => call<Dashboard[]>("dashboards"),
  kpis: (key: string) => call<Kpis>(`dashboards/${key}/kpis`),
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

/** One live event of a running job (GET /jobs/{id}/events, server-sent events). */
export type LiveStep = { key: string; label: string; state: "running" | "done" | "failed" | "no_result"; ms?: number };
/** The events of the job event stream, as written by the API (see orchestrator/wbg_orchestrator/progress.py). */
export type LiveEvent =
  | ({ type: "step" } & LiveStep)
  | { type: "text"; text: string }                 // a piece of the reply, as Claude writes it
  | { type: "text_reset" }                         // the reply starts over (a retried model call)
  | { type: "answer"; text: string }               // the final answer, after the evidence check
  | { type: "done"; state: string; status: string | null };

/** Streams a job's live events to `onEvent` until it ends. Resolves true when the job's "done" event arrived, false
 *  if the stream broke first (the caller then falls back to polling). */
export async function streamJob(jobId: string, onEvent: (event: LiveEvent) => void): Promise<boolean> {
  const response = await fetch(`/api/backend/jobs/${jobId}/events`, { cache: "no-store" }).catch(() => null);
  if (!response?.ok || !response.body) return false;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  // Server-sent events arrive in chunks that can split an event, so text is buffered and cut at each blank line.
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read().catch(() => ({ value: undefined, done: true }));
    if (done) return false;                         // the stream closed before the "done" event
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (!data) continue;                          // a keep-alive comment
      const event = JSON.parse(data) as LiveEvent;
      onEvent(event);
      if (event.type === "done") return true;
    }
  }
}

/** Polls a background job until it finishes (the API runs every request as a job). Quick replies (greetings, fixed
 *  plans) finish in well under a second, so the first checks come fast and the interval grows to a second. */
export async function waitForJob(job: Job, onTick?: (job: Job) => void, timeoutMs = 300_000): Promise<Job> {
  const started = Date.now();
  let current = job;
  let delay = 150;
  while (current.state === "running" && Date.now() - started < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(delay * 1.5, 1000);
    current = await api.job(current.job_id);
    onTick?.(current);
  }
  return current;
}
