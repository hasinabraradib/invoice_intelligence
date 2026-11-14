// Rules for the server-side API proxy (src/app/api/[...path]/route.ts), kept here so they
// can be unit-tested without a server.

/** First path segment the proxy forwards. Anything else is a 404 on this side. */
const ROOTS = new Set(["health", "documents", "invoices", "assistant", "search", "cross-checks", "jobs", "stats", "evaluation", "openapi.json"]);

export function isAllowedPath(segments: string[]): boolean {
  if (segments.length === 0 || !ROOTS.has(segments[0])) return false;
  // Segments arrive decoded: refuse anything that could change the target path.
  return segments.every((s) => s !== "" && s !== "." && s !== ".." && !/[/\\?#\0]/.test(s));
}

/** Request headers passed on to the API. Cookies, auth headers and the browser's own
 * idea of the host are dropped; the key and the user's address are added. */
const REQUEST_HEADERS = ["content-type", "content-length", "accept", "last-event-id"];

export function forwardHeaders(incoming: Headers, apiKey: string): Headers {
  const out = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = incoming.get(name);
    if (value) out.set(name, value);
  }
  out.set("X-API-Key", apiKey);
  const client = (incoming.get("x-forwarded-for") ?? "").split(",")[0].trim() || incoming.get("x-real-ip") || "";
  if (client) out.set("X-Forwarded-For", client);
  return out;
}

/** Response headers passed back to the browser. The API's security headers are replaced
 * by this server's own (see src/proxy.ts and next.config.ts). */
const RESPONSE_HEADERS = ["content-type", "content-disposition", "content-length", "cache-control", "retry-after", "etag", "last-modified"];

export function pickResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.get(name);
    if (value) out.set(name, value);
  }
  out.set("X-Content-Type-Options", "nosniff");
  if ((upstream.get("content-type") ?? "").includes("text/event-stream")) {
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
  }
  return out;
}
