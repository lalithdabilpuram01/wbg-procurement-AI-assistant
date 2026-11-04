// Server-only helpers: the API token lives in an httpOnly cookie and is attached here, never exposed to the browser.
/** Name of the httpOnly cookie that holds the API session token. */
export const SESSION_COOKIE = "wbg_session";
/** Base URL of the FastAPI app (orchestrator/wbg_orchestrator/api.py); WBG_API_URL overrides the local default. */
export const API_URL = process.env.WBG_API_URL ?? "http://127.0.0.1:8080";
/** First path segments the /api/backend proxy may forward. Anything else, such as /auth, is answered with 404. */
export const ALLOWED_PREFIXES = ["conversations", "jobs", "folders", "bookmarks", "health", "me", "learning", "reviews", "projects", "drafts", "lessons", "dashboards"];

/** Request headers for the API: the bearer token, plus a Content-Type when the request has a body. */
export function backendHeaders(token: string, contentType?: string | null): HeadersInit {
  return { Authorization: `Bearer ${token}`, ...(contentType ? { "Content-Type": contentType } : {}) };
}
