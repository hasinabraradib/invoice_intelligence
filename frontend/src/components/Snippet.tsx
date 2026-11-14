import { Fragment, type ReactNode } from "react";

// The API sends snippets HTML-escaped, with matches wrapped in <mark>...</mark>. We turn
// ONLY those tags into React elements and everything else into plain text. No
// dangerouslySetInnerHTML: text like "<script>" can only ever be shown, never run.

const ENTITIES: Record<string, string> = {
  "&lt;": "<",
  "&gt;": ">",
  "&amp;": "&",
  "&quot;": '"',
  "&#x27;": "'",
  "&#39;": "'",
};

export function unescapeHtml(text: string): string {
  return text.replace(/&(lt|gt|amp|quot|#x27|#39);/g, (entity) => ENTITIES[entity] ?? entity);
}

export function renderSnippet(snippet: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let marked = false;
  snippet.split(/(<mark>|<\/mark>)/).forEach((part, index) => {
    if (part === "<mark>") marked = true;
    else if (part === "</mark>") marked = false;
    else if (part) {
      const text = unescapeHtml(part);
      nodes.push(
        marked ? (
          <mark key={index} className="rounded bg-warning-soft px-0.5 text-ink">
            {text}
          </mark>
        ) : (
          <Fragment key={index}>{text}</Fragment>
        ),
      );
    }
  });
  return nodes;
}

export function Snippet({ text }: { text: string }) {
  return <p className="text-sm text-ink-2">{renderSnippet(text)}</p>;
}
