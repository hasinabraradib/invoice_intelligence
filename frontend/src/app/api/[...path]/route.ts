// Server-side proxy to the API. The browser calls /api/* on this origin; this handler
// forwards the request to the API over the private network and adds the API key, which
// exists only in this server's environment (API_KEY), never in the browser bundle.
//
// Only known API paths are forwarded, request and response headers are allow-listed, and
// bodies are streamed both ways (multipart uploads in, Server-Sent Events out).
import type { NextRequest } from "next/server";
import { forwardHeaders, isAllowedPath, pickResponseHeaders } from "@/lib/proxy";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };

function error(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

async function handle(request: NextRequest, context: Context): Promise<Response> {
  const base = process.env.API_INTERNAL_URL;
  const key = process.env.API_KEY;
  if (!base || !key) {
    return error(503, "proxy_not_configured", "The web server has no API_INTERNAL_URL or API_KEY configured.");
  }
  const { path } = await context.params;
  if (!isAllowedPath(path)) return error(404, "not_found", "Not found");

  const target = new URL(`/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, base);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: forwardHeaders(request.headers, key),
      body: hasBody ? request.body : undefined,
      // Stream the request body instead of buffering it (needed for large uploads).
      ...(hasBody ? { duplex: "half" } : {}),
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
    } as RequestInit);
  } catch {
    return error(502, "api_unreachable", "The API is not reachable right now. Try again in a moment.");
  }
  return new Response(upstream.body, { status: upstream.status, headers: pickResponseHeaders(upstream.headers) });
}

export { handle as GET, handle as POST, handle as PATCH, handle as PUT, handle as DELETE };
