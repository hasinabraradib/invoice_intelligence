import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle: the Docker image needs no node_modules at runtime.
  output: "standalone",
  poweredByHeader: false,
  // Defaults from create-next-app.
  cacheComponents: true,
  partialPrefetching: true,
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
