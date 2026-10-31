import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/server";

// Pages need a session; without one the user is sent to sign in. API routes check the cookie themselves.
export function middleware(request: NextRequest) {
  if (!request.cookies.get(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!login|signup|forgot-password|api|_next|favicon.ico).*)"] };
