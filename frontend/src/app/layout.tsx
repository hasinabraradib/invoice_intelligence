import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { Providers } from "@/components/Providers";

export const metadata: Metadata = {
  title: "Invoice Intelligence",
  description: "Upload invoices, review extracted fields with evidence, and search them.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
          Skip to content
        </a>
        <Providers>
          {/* The nav reads the current URL, so it must sit inside a Suspense boundary. */}
          <Suspense fallback={<Nav pathname="" />}>
            <Nav />
          </Suspense>
          <main id="main" className="mx-auto max-w-7xl px-4 py-6">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
