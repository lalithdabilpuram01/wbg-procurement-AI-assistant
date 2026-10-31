import { NextResponse } from "next/server";
import { callAuth, fields, relayError, startSession } from "@/lib/session";

// Forgot password: username + recovery code + new password. The API sets the new password, signs the user out
// everywhere else and returns a new session and a NEW recovery code (the one just used no longer works).
export async function POST(request: Request) {
  const body = fields(await request.json().catch(() => null), ["username", "recovery_code", "new_password"]);
  if (!body) return NextResponse.json({ detail: "All fields are required." }, { status: 400 });
  const reset = await callAuth("reset", body);
  if (!reset?.ok) return relayError(reset, "The password could not be reset.");
  const { token, recovery_code } = (await reset.json()) as { token: string; recovery_code: string };
  await startSession(token);
  return NextResponse.json({ recovery_code });
}
