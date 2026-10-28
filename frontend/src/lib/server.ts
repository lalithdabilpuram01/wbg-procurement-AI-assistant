// Server-only helpers: the API token lives in an httpOnly cookie and is attached here, never exposed to the browser.
export const SESSION_COOKIE = "wbg_session";
export const API_URL = process.env.WBG_API_URL ?? "http://127.0.0.1:8080";
export const ALLOWED_PREFIXES = ["conversations", "jobs", "folders", "bookmarks", "health", "me", "learning", "reviews", "projects"];

export function backendHeaders(token: string, contentType?: string | null): HeadersInit {
  return { Authorization: `Bearer ${token}`, ...(contentType ? { "Content-Type": contentType } : {}) };
}
