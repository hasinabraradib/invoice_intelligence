"use client";
import { useState } from "react";
import { pageImageUrl } from "@/lib/api";
import { FileTypeIcon } from "./ui";

/** Page 1 as a small thumbnail (lazy). The file-type icon shows until the image has
 * loaded, and stays if it can't (spreadsheets, failed renders, no pages yet). */
export function DocThumb({
  id,
  mime,
  filename,
  pageCount,
  status,
}: {
  id: string;
  mime: string;
  filename: string;
  pageCount: number;
  status: string;
}) {
  const [state, setState] = useState<"loading" | "loaded" | "failed">("loading");
  const previewable = /pdf|image/i.test(mime) && pageCount > 0 && status !== "uploaded" && status !== "processing";
  return (
    <span className="relative grid h-12 w-10 shrink-0 place-items-center">
      {state !== "loaded" ? <FileTypeIcon mime={mime} filename={filename} /> : null}
      {previewable && state !== "failed" ? (
        // eslint-disable-next-line @next/next/no-img-element -- API-rendered PNG, not a static asset
        <img
          src={pageImageUrl(id, 1)}
          alt=""
          loading="lazy"
          decoding="async"
          onLoad={() => setState("loaded")}
          onError={() => setState("failed")}
          className={`absolute inset-0 h-12 w-10 rounded-lg border border-line bg-white object-cover object-top shadow-soft transition-opacity duration-300 dark:opacity-85 ${
            state === "loaded" ? "opacity-100" : "opacity-0"
          }`}
        />
      ) : null}
    </span>
  );
}
