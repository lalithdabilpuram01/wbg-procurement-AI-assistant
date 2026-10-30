import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { ALLOWED_PREFIXES, API_URL, SESSION_COOKIE, backendHeaders } from "@/lib/server";

type Context = { params: Promise<{ path: string[] }> };

// Same-origin proxy to the FastAPI app: allow-listed paths only, the session token attached server-side.
async function forward(request: Request, { params }: Context) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "authentication required" }, { status: 401 });
  const { path } = await params;
  if (!ALLOWED_PREFIXES.includes(path[0]) || path.some((p) => p === ".." || p.includes("/"))) {
    return NextResponse.json({ detail: "not found" }, { status: 404 });
  }
  const url = `${API_URL}/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`;
  const hasBody = !["GET", "HEAD", "DELETE"].includes(request.method);
  const upstream = await fetch(url, {
    method: request.method,
    headers: backendHeaders(token, hasBody ? "application/json" : null),
    body: hasBody ? await request.text() : undefined,
    cache: "no-store",
  }).catch(() => null);
  if (!upstream) return NextResponse.json({ detail: "the API is not reachable" }, { status: 502 });
  if (upstream.status === 204) return new Response(null, { status: 204 });
  const type = upstream.headers.get("Content-Type") ?? "";
  if (type.startsWith("text/event-stream")) {                      // live job events: passed through as they arrive
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
    });
  }
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE };
