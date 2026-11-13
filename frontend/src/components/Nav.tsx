"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Documents" },
  { href: "/review", label: "Review queue" },
  { href: "/search", label: "Search" },
  { href: "/cross-check", label: "Cross-check" },
];

export function Nav(props: { pathname?: string }) {
  return props.pathname !== undefined ? <NavBar pathname={props.pathname} /> : <CurrentNav />;
}

function CurrentNav() {
  return <NavBar pathname={usePathname()} />;
}

function NavBar({ pathname }: { pathname: string }) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <nav aria-label="Main" className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="focus-ring rounded font-semibold text-slate-900">
          Invoice Intelligence
        </Link>
        <ul className="flex flex-wrap gap-1">
          {LINKS.map((link) => {
            const active =
              link.href === "/"
                ? pathname === "/" || pathname.startsWith("/documents")
                : pathname.startsWith(link.href);
            return (
              <li key={link.href}>
                <Link
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={`focus-ring rounded px-3 py-1.5 text-sm ${
                    active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
                  }`}
                >
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
