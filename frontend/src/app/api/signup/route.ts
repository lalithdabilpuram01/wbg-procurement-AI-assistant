// API route POST /api/signup, used by the sign-up page; it calls POST /auth/signup on the API.
import { NextResponse } from "next/server";
import { callAuth, fields, relayError, startSession } from "@/lib/session";

// Create an account: the API checks the username and password, creates a normal user and returns a session token
// and a recovery code. The token goes into the session cookie; the recovery code is shown to the user once.
export async function POST(request: Request) {
  const body = fields(await request.json().catch(() => null), ["username", "password"]);
  if (!body) return NextResponse.json({ detail: "Username and password are required." }, { status: 400 });
  const created = await callAuth("signup", body);
  if (!created?.ok) return relayError(created, "The account could not be created.");
  const { token, recovery_code } = (await created.json()) as { token: string; recovery_code: string };
  await startSession(token);
  return NextResponse.json({ recovery_code }, { status: 201 });
}
