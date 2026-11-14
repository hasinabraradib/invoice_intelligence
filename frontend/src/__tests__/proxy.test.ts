import { describe, expect, it } from "vitest";
import { forwardHeaders, isAllowedPath, pickResponseHeaders } from "@/lib/proxy";
import { buildCsp } from "@/proxy";
import { API_URL } from "@/lib/api";

describe("API proxy rules", () => {
  it("forwards only known API paths", () => {
    expect(isAllowedPath(["documents"])).toBe(true);
    expect(isAllowedPath(["documents", "abc", "pages", "1", "image"])).toBe(true);
    expect(isAllowedPath(["assistant", "conversations", "x", "messages"])).toBe(true);
    expect(isAllowedPath(["openapi.json"])).toBe(true);
    expect(isAllowedPath([])).toBe(false);
    expect(isAllowedPath(["admin"])).toBe(false);
    expect(isAllowedPath(["documents", ".."])).toBe(false);
    expect(isAllowedPath(["documents", "..", "..", "etc"])).toBe(false);
    expect(isAllowedPath(["documents", "a/b"])).toBe(false);
    expect(isAllowedPath(["documents", "a?b"])).toBe(false);
  });

  it("adds the key and the user's address, and drops cookies and auth headers", () => {
    const incoming = new Headers({
      "content-type": "application/json",
      cookie: "session=secret",
      authorization: "Bearer x",
      "x-api-key": "from-the-browser",
      "x-forwarded-for": "203.0.113.7, 10.0.0.1",
      host: "evil.example",
    });
    const out = forwardHeaders(incoming, "server-key");
    expect(out.get("x-api-key")).toBe("server-key");
    expect(out.get("x-forwarded-for")).toBe("203.0.113.7");
    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("cookie")).toBeNull();
    expect(out.get("authorization")).toBeNull();
    expect(out.get("host")).toBeNull();
  });

  it("passes back only safe response headers and keeps SSE unbuffered", () => {
    const out = pickResponseHeaders(
      new Headers({ "content-type": "text/event-stream", "set-cookie": "a=b", server: "uvicorn", "retry-after": "12" }),
    );
    expect(out.get("set-cookie")).toBeNull();
    expect(out.get("server")).toBeNull();
    expect(out.get("retry-after")).toBe("12");
    expect(out.get("cache-control")).toContain("no-transform");
    expect(out.get("x-content-type-options")).toBe("nosniff");
  });

  it("never points the browser at the API directly", () => {
    expect(API_URL).toBe("/api");
  });
});

describe("page CSP", () => {
  it("allows scripts only from this origin or with the nonce", () => {
    const csp = buildCsp("abc123", false);
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(buildCsp("n", true)).toContain("'unsafe-eval'"); // dev only (React debugging)
  });
});
