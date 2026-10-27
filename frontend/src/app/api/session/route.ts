import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE, backendHeaders } from "@/lib/server";

// Sign in: the token is checked against the API, then kept in an httpOnly cookie (never readable by page scripts).
export async function POST(request: Request) {
  const { token } = (await request.json().catch(() => ({}))) as { token?: string };
  if (!token || token.length > 200) return NextResponse.json({ detail: "token required" }, { status: 400 });
  const check = await fetch(`${API_URL}/conversations`, { headers: backendHeaders(token), cache: "no-store" }).catch(() => null);
  if (!check) return NextResponse.json({ detail: "the API is not reachable" }, { status: 502 });
  if (!check.ok) return NextResponse.json({ detail: "invalid token" }, { status: 401 });
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 12,
  });
  return NextResponse.json({ ok: true });
}

export async function DELETE() {
  (await cookies()).delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
