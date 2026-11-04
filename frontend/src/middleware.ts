// Next.js middleware: runs before every matched request and guards the app pages behind the session cookie.
import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/server";

// Pages need a session; without one the user is sent to sign in. API routes check the cookie themselves.
export function middleware(request: NextRequest) {
  if (!request.cookies.get(SESSION_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

// Excluded: the sign-in, sign-up and password-reset pages (they must work signed out), the API routes and Next.js
// assets. Only the cookie's presence is checked here; the API validates the token on every proxied call.
export const config = { matcher: ["/((?!login|signup|forgot-password|api|_next|favicon.ico).*)"] };
