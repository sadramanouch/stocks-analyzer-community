import { NextRequest } from "next/server";
import { COOKIE_NAME, authConfigured, localAccessAllowed, sameOrigin, validSession } from "@/lib/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, context: Context) {
  if (!authConfigured() && !localAccessAllowed()) return Response.json({ detail: "Authentication is not configured" }, { status: 503 });
  if (!await validSession(request.cookies.get(COOKIE_NAME)?.value)) return Response.json({ detail: "Sign in required" }, { status: 401 });
  if (!sameOrigin(request)) return Response.json({ detail: "Cross-site request rejected" }, { status: 403 });
  const { path } = await context.params;
  if (path[0] !== "api" || path.some(part => !part || part === "." || part === ".." || /[\\/%?#\x00]/.test(part))) {
    return Response.json({ detail: "Invalid API path" }, { status: 400 });
  }
  if (!process.env.API_SECRET && process.env.FLY_APP_NAME) return Response.json({ detail: "Backend authentication is not configured" }, { status: 503 });
  const target = new URL(`/${path.map(encodeURIComponent).join("/")}`, process.env.API_URL || "http://127.0.0.1:8000");
  target.search = request.nextUrl.search;
  target.searchParams.delete("api_key");
  const headers = new Headers({ Accept: request.headers.get("accept") || "application/json" });
  if (process.env.API_SECRET) headers.set("X-API-Key", process.env.API_SECRET);
  if (request.headers.has("content-type")) headers.set("Content-Type", request.headers.get("content-type")!);
  let body: Uint8Array | undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (reader) {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 1024 * 1024) { await reader.cancel(); return Response.json({ detail: "Request too large" }, { status: 413 }); }
        chunks.push(chunk.value);
      }
    }
    body = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  }
  try {
    const response = await fetch(target, { method: request.method, headers, body: body as BodyInit | undefined,
      redirect: "manual", cache: "no-store", signal: AbortSignal.any([request.signal, AbortSignal.timeout(900_000)]) });
    if (response.status >= 300 && response.status < 400) throw new Error("Unexpected upstream redirect");
    const sourceHash = response.headers.get("x-content-sha256");
    return new Response(response.body, { status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store", "X-Accel-Buffering": "no",
      ...(sourceHash && /^[a-f0-9]{64}$/.test(sourceHash) ? { "X-Content-SHA256": sourceHash } : {}),
    } });
  } catch {
    return Response.json({ detail: "Backend unavailable. Please retry." }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as PATCH, forward as DELETE, forward as HEAD };
