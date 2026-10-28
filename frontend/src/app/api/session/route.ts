import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE } from "@/lib/server";

// Sign in: the username and password are checked by the API, which returns a session token; the token is kept in an
// httpOnly cookie (never readable by page scripts). The password is not stored here.
export async function POST(request: Request) {
  const { username, password } = (await request.json().catch(() => ({}))) as { username?: string; password?: string };
  if (!username || !password || username.length > 64 || password.length > 200) {
    return NextResponse.json({ detail: "username and password required" }, { status: 400 });
  }
  const login = await fetch(`${API_URL}/auth/login`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }), cache: "no-store",
  }).catch(() => null);
  if (!login) return NextResponse.json({ detail: "the API is not reachable" }, { status: 502 });
  if (!login.ok) return NextResponse.json({ detail: "wrong username or password" }, { status: 401 });
  const { token } = (await login.json()) as { token: string };
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 12,
  });
  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
