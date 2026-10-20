/** Backend credentials stay on the Next server; browsers use the signed session. */
export function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const parsed = new URL(url, "http://localhost");
  if (!parsed.pathname.startsWith("/api/")) throw new Error("Invalid backend API path");
  parsed.searchParams.delete("api_key");
  const headers = new Headers(init.headers);
  headers.delete("X-API-Key");
  return fetch(`/api/backend${parsed.pathname}${parsed.search}`, { ...init, headers, credentials: "same-origin", cache: "no-store" });
}
