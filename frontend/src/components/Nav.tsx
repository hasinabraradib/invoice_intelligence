"use client";
// Slim sidebar with icons and labels on desktop; a bottom tab bar on phones.
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ClipboardCheck,
  FileStack,
  GitCompareArrows,
  LayoutDashboard,
  MessageSquare,
  Search,
  type LucideIcon,
} from "lucide-react";

const LINKS: { href: string; label: string; icon: LucideIcon; match: (p: string) => boolean }[] = [
  { href: "/", label: "Assistant", icon: MessageSquare, match: (p) => p === "/" },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, match: (p) => p.startsWith("/dashboard") },
  { href: "/documents", label: "Documents", icon: FileStack, match: (p) => p.startsWith("/documents") },
  { href: "/review", label: "Review", icon: ClipboardCheck, match: (p) => p.startsWith("/review") },
  { href: "/cross-check", label: "Cross-check", icon: GitCompareArrows, match: (p) => p.startsWith("/cross-check") },
  { href: "/search", label: "Search", icon: Search, match: (p) => p.startsWith("/search") },
];

export function Nav(props: { pathname?: string }) {
  return props.pathname !== undefined ? <Sidebar pathname={props.pathname} /> : <CurrentSidebar />;
}

function CurrentSidebar() {
  return <Sidebar pathname={usePathname()} />;
}

function Logo() {
  return (
    <span aria-hidden="true" className="grid size-8 place-items-center rounded-xl bg-accent text-on-accent shadow-soft">
      <svg viewBox="0 0 20 20" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
        <path d="M6 3.5h6l3 3V16a.5.5 0 0 1-.5.5h-8.5A.5.5 0 0 1 5.5 16V4a.5.5 0 0 1 .5-.5Z" />
        <path d="M8.5 10h4M8.5 13h2.5" />
      </svg>
    </span>
  );
}

function Sidebar({ pathname }: { pathname: string }) {
  return (
    <>
      {/* Desktop and tablet: a slim column. */}
      <header className="hidden md:sticky md:top-0 md:flex md:h-screen md:w-60 md:shrink-0 md:flex-col md:border-r md:border-line md:bg-surface/60 md:px-3 md:py-5 md:backdrop-blur">
        <Link href="/" className="focus-ring mb-8 flex items-center gap-2.5 rounded-xl px-2 py-1">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight text-ink">Invoice Intelligence</span>
        </Link>
        <nav aria-label="Main">
          <ul className="flex flex-col gap-0.5">
            {LINKS.map(({ href, label, icon: Icon, match }) => {
              const active = match(pathname);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={`focus-ring group flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors ${
                      active ? "bg-surface-2 font-medium text-ink shadow-soft" : "text-muted hover:bg-surface-2 hover:text-ink"
                    }`}
                  >
                    <Icon
                      aria-hidden="true"
                      className={`size-[18px] ${active ? "text-accent" : "text-faint group-hover:text-ink-2"}`}
                      strokeWidth={1.75}
                    />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <p className="mt-auto px-3 text-xs text-faint">Every value has evidence.</p>
      </header>

      {/* Phones: a top bar with the name, and a fixed bottom tab bar. */}
      <header className="sticky top-0 z-30 flex items-center gap-2.5 border-b border-line bg-bg/85 px-4 py-3 backdrop-blur md:hidden">
        <Link href="/" className="focus-ring flex items-center gap-2.5 rounded-xl">
          <Logo />
          <span className="font-semibold tracking-tight text-ink">Invoice Intelligence</span>
        </Link>
      </header>
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface/90 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        <ul className="grid grid-cols-6">
          {LINKS.map(({ href, label, icon: Icon, match }) => {
            const active = match(pathname);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`focus-ring flex flex-col items-center gap-1 rounded-lg px-1 pb-2 pt-2.5 text-[10px] font-medium ${
                    active ? "text-accent-ink" : "text-muted"
                  }`}
                >
                  <Icon aria-hidden="true" className="size-5" strokeWidth={active ? 2 : 1.75} />
                  <span className="max-w-full truncate">{label}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </>
  );
}
