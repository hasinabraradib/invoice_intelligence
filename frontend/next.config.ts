import type { NextConfig } from "next";

// Headers for every response, including the /api proxy and static files. The page CSP
// (with a per-request nonce) is set in src/proxy.ts.
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
];

const nextConfig: NextConfig = {
  // Self-contained server bundle: the Docker image needs no node_modules at runtime.
  output: "standalone",
  poweredByHeader: false,
  // Pages are rendered per request (no prerendered shell): a nonce-based CSP needs it, and
  // every page here loads its data in the browser anyway.
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
  turbopack: {
    rules: {
      // Tailwind CSS is compiled by its Turbopack loader (there is no PostCSS config).
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
