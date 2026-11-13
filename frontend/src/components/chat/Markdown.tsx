// Safe Markdown for model answers: a small subset (paragraphs, lists, headings, bold,
// italic, code, links) turned into React elements. No HTML is ever interpreted:
// "<script>" in the text is shown as text. Links only for http(s) and in-app paths.
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|_[^_\s][^_]*_|\[[^\]]+\]\([^)\s]+\))/g;

export function safeHref(url: string): string | null {
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

export function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (!part) return null;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1)
      return <code key={i} className="rounded bg-slate-100 px-1 text-[0.9em]">{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{inline(part.slice(2, -2))}</strong>;
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_")))
      if (part.length > 2) return <em key={i}>{inline(part.slice(1, -1))}</em>;
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part);
    if (link) {
      const href = safeHref(link[2]);
      if (!href) return <Fragment key={i}>{link[1]}</Fragment>;
      return href.startsWith("/")
        ? <Link key={i} href={href} className="btn-link">{link[1]}</Link>
        : <a key={i} href={href} target="_blank" rel="noopener noreferrer nofollow" className="btn-link">{link[1]}</a>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={blocks.length} className="overflow-x-auto rounded bg-slate-100 p-2 text-xs"><code>{code.join("\n")}</code></pre>);
    } else if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={blocks.length} className={`${ordered ? "list-decimal" : "list-disc"} space-y-1 pl-5`}>
        {items.map((item, n) => <li key={n}>{inline(item)}</li>)}</List>);
    } else if (/^#{1,3}\s/.test(line)) {
      blocks.push(<p key={blocks.length} className="font-semibold">{inline(line.replace(/^#{1,3}\s/, ""))}</p>);
      i++;
    } else if (line.trim() === "") {
      i++;
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^(\s*([-*]|\d+\.)\s+|#{1,3}\s|```)/.test(lines[i])) para.push(lines[i++]);
      blocks.push(<p key={blocks.length}>{para.map((p, n) => <Fragment key={n}>{n > 0 ? <br /> : null}{inline(p)}</Fragment>)}</p>);
    }
  }
  return <div className="space-y-2 break-words">{blocks}</div>;
}
