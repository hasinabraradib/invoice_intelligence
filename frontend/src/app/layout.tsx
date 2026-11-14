import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "Invoice Intelligence",
  description: "Upload invoices, review extracted fields with evidence, and search them.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5efe4" },
    { media: "(prefers-color-scheme: dark)", color: "#161411" },
  ],
};

// Rendered per request so Next.js can put the CSP nonce (src/proxy.ts) on its scripts.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-xl focus:bg-surface focus:px-3 focus:py-2 focus:text-ink focus:shadow-lift"
        >
          Skip to content
        </a>
        <Providers>
          <div className="md:flex">
            {/* The nav reads the current URL, so it must sit inside a Suspense boundary. */}
            <Suspense fallback={<Nav pathname="" />}>
              <Nav />
            </Suspense>
            <main id="main" className="min-w-0 flex-1 px-4 pb-28 pt-6 sm:px-6 md:px-8 md:pb-12 md:pt-10 lg:px-10">
              <div className="mx-auto max-w-6xl">{children}</div>
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
