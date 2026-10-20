import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, MAX_AGE, authConfigured, createSession, localAccessAllowed, passwordMatches, sameOrigin } from "@/lib/session";

// Per-process defence; production perimeter rate limiting is still recommended.
const attempts = new Map<string, { count: number; until: number }>();

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ error: "Cross-site request rejected" }, { status: 403 });
  if (!authConfigured()) return NextResponse.json(localAccessAllowed() ? { ok: true } : { error: "Authentication is not configured" }, { status: localAccessAllowed() ? 200 : 503 });
  const address = process.env.FLY_APP_NAME ? request.headers.get("fly-client-ip") || "unknown" : "local";
  const now = Date.now();
  for (const [key, value] of attempts) if (value.until <= now) attempts.delete(key);
  const attempt = attempts.get(address) || { count: 0, until: now + 15 * 60_000 };
  if (attempt.count >= 10) return NextResponse.json({ error: "Too many attempts. Try again later." }, { status: 429, headers: { "Retry-After": "900" } });
  if (attempts.size >= 10_000 && !attempts.has(address)) return NextResponse.json({ error: "Too many attempts. Try again later." }, { status: 429 });
  if (Number(request.headers.get("content-length")) > 8192) return NextResponse.json({ error: "Request too large" }, { status: 413 });
  let password: unknown;
  try {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (reader) while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > 8192) { await reader.cancel(); return NextResponse.json({ error: "Request too large" }, { status: 413 }); }
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    password = JSON.parse(new TextDecoder().decode(bytes)).password;
  }
  catch { return NextResponse.json({ error: "Invalid request" }, { status: 400 }); }
  if (!await passwordMatches(password)) {
    attempts.set(address, { ...attempt, count: attempt.count + 1 });
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }
  attempts.delete(address);
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(COOKIE_NAME, await createSession(), { httpOnly: true, secure: Boolean(process.env.FLY_APP_NAME) || request.nextUrl.protocol === "https:", sameSite: "strict", maxAge: MAX_AGE, path: "/" });
  return response;
}
