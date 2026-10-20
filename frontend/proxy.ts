import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, authConfigured, localAccessAllowed, validSession } from "./lib/session";

export async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  if (path === "/login" || path === "/api/auth/login") return NextResponse.next();
  if (!authConfigured() && !localAccessAllowed()) return NextResponse.json({ detail: "Authentication is not configured" }, { status: 503 });
  if (await validSession(request.cookies.get(COOKIE_NAME)?.value)) return NextResponse.next();
  if (path.startsWith("/api/")) return NextResponse.json({ detail: "Sign in required" }, { status: 401 });
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
