// API route /api/session: POST signs in through the API's POST /auth/login, DELETE signs out.
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/server";
import { callAuth, fields, relayError, startSession } from "@/lib/session";

// Sign in: the username and password are checked by the API, which returns a session token; the token is kept in an
// httpOnly cookie (never readable by page scripts). The password is not stored here.
export async function POST(request: Request) {
  const body = fields(await request.json().catch(() => null), ["username", "password"]);
  if (!body || body.username.length > 64) return NextResponse.json({ detail: "username and password required" }, { status: 400 });
  const login = await callAuth("login", body);
  if (!login?.ok) return relayError(login, "wrong username or password");
  const { token } = (await login.json()) as { token: string };
  await startSession(token);
  return NextResponse.json({ ok: true });
}

// Sign out: removes the session cookie, so the next page load is redirected to /login by the middleware.
export async function DELETE() {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
