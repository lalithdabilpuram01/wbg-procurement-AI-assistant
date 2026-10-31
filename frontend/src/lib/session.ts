import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE } from "@/lib/server";

// Route-handler helpers for the sign-in pages: call an /auth endpoint of the API, and keep the session token it
// returns in an httpOnly cookie (never readable by page scripts). Passwords and recovery codes are not stored here.

export async function callAuth(path: string, body: Record<string, string>): Promise<Response | null> {
  return fetch(`${API_URL}/auth/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store",
  }).catch(() => null);
}

export async function startSession(token: string): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 12,
  });
}

// The API's own message for refusals the user can fix (taken username, weak password, too many attempts).
export async function relayError(response: Response | null, fallback: string): Promise<NextResponse> {
  if (!response) return NextResponse.json({ detail: "The API is not reachable." }, { status: 502 });
  const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === "string" ? body.detail : fallback;
  return NextResponse.json({ detail }, { status: response.status });
}

export function fields(input: unknown, names: string[], maxLength = 200): Record<string, string> | null {
  const record = (input ?? {}) as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const name of names) {
    const value = record[name];
    if (typeof value !== "string" || !value || value.length > maxLength) return null;
    out[name] = value;
  }
  return out;
}
