"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { uploadFile } from "@/lib/upload";
import { ACCEPTED_EXTENSIONS, checkUploadFile, MAX_UPLOAD_MB } from "@/lib/validation";
import { percent } from "@/lib/format";
import { errorMessage } from "./Toast";
import { FileTypeIcon, ProgressBar } from "./ui";
import { UploadCloud } from "lucide-react";

type Item = {
  key: string;
  name: string;
  progress: number;
  state: "uploading" | "done" | "duplicate" | "error";
  message?: string;
  documentId?: string;
};

export function Uploader() {
  const [items, setItems] = useState<Item[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const update = (key: string, patch: Partial<Item>) =>
    setItems((all) => all.map((item) => (item.key === key ? { ...item, ...patch } : item)));

  function start(files: FileList | null) {
    if (!files) return;
    for (const file of Array.from(files)) {
      const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`;
      const problem = checkUploadFile(file);
      setItems((all) => [
        { key, name: file.name, progress: 0, state: problem ? "error" : "uploading", message: problem ?? undefined },
        ...all,
      ]);
      if (problem) continue;
      uploadFile(file, (fraction) => update(key, { progress: fraction }))
        .then((result) => {
          if (result.error) {
            update(key, { state: "error", message: result.error.message });
          } else {
            update(key, {
              state: result.duplicate ? "duplicate" : "done",
              progress: 1,
              documentId: result.document_id ?? undefined,
            });
          }
          void queryClient.invalidateQueries({ queryKey: ["documents"] });
        })
        .catch((error: unknown) => update(key, { state: "error", message: errorMessage(error) }));
    }
  }

  return (
    <section aria-labelledby="upload-heading" className="card">
      <h2 id="upload-heading" className="sr-only">Upload invoices</h2>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          start(e.dataTransfer.files);
        }}
        className={`flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed px-6 py-10 text-center transition-colors ${
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface-2/50 hover:border-accent-line"
        }`}
      >
        <span aria-hidden="true" className="grid size-12 place-items-center rounded-2xl border border-line bg-surface text-accent-ink shadow-soft">
          <UploadCloud className="size-5" strokeWidth={1.75} />
        </span>
        <div>
          <p className="font-medium text-ink">Drop invoices here</p>
          <p className="mt-0.5 text-xs text-muted">PDF, PNG, JPEG, TIFF, XLSX, XLS or CSV · up to {MAX_UPLOAD_MB} MB each</p>
        </div>
        <label htmlFor="file-input" className="btn-primary cursor-pointer">Choose files</label>
        <input
          ref={input}
          id="file-input"
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="sr-only"
          onChange={(e) => {
            start(e.target.files);
            e.target.value = ""; // allow choosing the same file again
          }}
        />
      </div>
      {items.length > 0 ? (
        <ul className="mt-4 space-y-2" aria-label="Uploads">
          {items.map((item) => (
            <li key={item.key} className="animate-fade-up rounded-xl border border-line bg-surface p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2.5">
                  <FileTypeIcon filename={item.name} size="sm" />
                  <span className="min-w-0 truncate font-medium text-ink">{item.name}</span>
                </span>
                <span className="text-xs">
                  {item.state === "uploading" && <span className="num text-muted">Uploading {percent(item.progress)}</span>}
                  {item.state === "done" && item.documentId && (
                    <Link className="btn-link text-xs" href={`/documents/${item.documentId}`}>Uploaded · open</Link>
                  )}
                  {item.state === "duplicate" && item.documentId && (
                    <Link className="btn-link text-xs" href={`/documents/${item.documentId}`}>Already uploaded · open existing</Link>
                  )}
                  {item.state === "error" && <span className="text-danger">{item.message}</span>}
                </span>
              </div>
              {item.state === "uploading" ? (
                <div className="mt-2.5">
                  <ProgressBar value={item.progress} label={`Upload progress for ${item.name}`} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
