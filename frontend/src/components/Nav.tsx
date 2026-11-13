"use client";
// Sidebar navigation: a column on desktop, a top bar with a menu button on phones.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const LINKS = [
  { href: "/", label: "Assistant", match: (p: string) => p === "/" },
  { href: "/dashboard", label: "Dashboard", match: (p: string) => p.startsWith("/dashboard") },
  { href: "/documents", label: "Documents", match: (p: string) => p.startsWith("/documents") },
  { href: "/review", label: "Review", match: (p: string) => p.startsWith("/review") },
  { href: "/cross-check", label: "Cross-check", match: (p: string) => p.startsWith("/cross-check") },
  { href: "/search", label: "Search", match: (p: string) => p.startsWith("/search") },
];

export function Nav(props: { pathname?: string }) {
  return props.pathname !== undefined ? <Sidebar pathname={props.pathname} /> : <CurrentSidebar />;
}

function CurrentSidebar() {
  return <Sidebar pathname={usePathname()} />;
}

function Sidebar({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="border-b border-slate-200 bg-white md:sticky md:top-0 md:h-screen md:w-52 md:shrink-0 md:border-b-0 md:border-r">
      <div className="flex items-center justify-between px-4 py-3">
        <Link href="/" className="focus-ring rounded font-semibold text-slate-900">Invoice Intelligence</Link>
        <button type="button" className="btn-secondary px-2 py-1 md:hidden" aria-expanded={open}
          aria-controls="main-nav" onClick={() => setOpen((v) => !v)}>
          Menu
        </button>
      </div>
      <nav id="main-nav" aria-label="Main" className={`${open ? "block" : "hidden"} px-2 pb-3 md:block`}>
        <ul className="flex flex-col gap-1">
          {LINKS.map((link) => {
            const active = link.match(pathname);
            return (
              <li key={link.href}>
                <Link href={link.href} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)}
                  className={`focus-ring block rounded px-3 py-2 text-sm ${active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}>
                  {link.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
