export const COOKIE_NAME = "sa_session";
export const MAX_AGE = 60 * 60 * 24 * 30;
const encoder = new TextEncoder();

export function localAccessAllowed() {
  return !process.env.FLY_APP_NAME && (process.env.ALLOW_UNAUTHENTICATED_LOCAL === "1" || process.env.NODE_ENV === "development");
}

export function authConfigured() {
  return Boolean(process.env.APP_PASSWORD);
}

function encode(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decode(value: string) {
  return Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0));
}

async function key() {
  if (!authConfigured()) throw new Error("Authentication is not configured");
  const secret = `stock-analyzer-session-v1:${process.env.SESSION_SECRET ?? ""}:${process.env.APP_PASSWORD}`;
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function createSession(now = Date.now()) {
  const payload = encode(encoder.encode(JSON.stringify({ v: 1, exp: Math.floor(now / 1000) + MAX_AGE,
    nonce: encode(crypto.getRandomValues(new Uint8Array(16))) })));
  const signature = await crypto.subtle.sign("HMAC", await key(), encoder.encode(payload));
  return `${payload}.${encode(new Uint8Array(signature))}`;
}

export async function validSession(token: string | undefined, now = Date.now()) {
  if (!authConfigured()) return localAccessAllowed();
  if (!token || token.length > 1024) return false;
  try {
    const parts = token.split(".");
    if (parts.length !== 2) return false;
    const [payload, signature] = parts;
    if (!await crypto.subtle.verify("HMAC", await key(), decode(signature), encoder.encode(payload))) return false;
    const data = JSON.parse(new TextDecoder().decode(decode(payload)));
    const seconds = Math.floor(now / 1000);
    return data.v === 1 && typeof data.nonce === "string" && Number.isInteger(data.exp)
      && data.exp > seconds && data.exp <= seconds + MAX_AGE + 60;
  } catch { return false; }
}

export async function passwordMatches(input: unknown) {
  if (typeof input !== "string" || input.length > 4096 || !authConfigured()) return false;
  const a = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(input)));
  const b = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(process.env.APP_PASSWORD)));
  return a.reduce((difference, value, index) => difference | (value ^ b[index]), 0) === 0;
}

export function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  const site = request.headers.get("sec-fetch-site");
  const url = new URL(request.url);
  // Next can normalize request.url to localhost behind its server. The browser's
  // Host remains the local origin; hosted deployments set APP_ORIGIN explicitly.
  const expected = process.env.APP_ORIGIN || `${url.protocol}//${request.headers.get("host") || url.host}`;
  const writes = !["GET", "HEAD", "OPTIONS"].includes(request.method.toUpperCase());
  return site !== "cross-site" && (writes ? origin === expected : !origin || origin === expected);
}
