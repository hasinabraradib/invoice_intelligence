"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { uploadFile } from "@/lib/upload";
import { ACCEPTED_EXTENSIONS, checkUploadFile, MAX_UPLOAD_MB } from "@/lib/validation";
import { percent } from "@/lib/format";
import { errorMessage } from "./Toast";

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
      <h2 id="upload-heading" className="mb-3 text-lg font-semibold">Upload invoices</h2>
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
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 text-center ${
          dragging ? "border-blue-500 bg-blue-50" : "border-slate-300"
        }`}
      >
        <p className="text-sm text-slate-700">Drag files here, or</p>
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
        <p className="text-xs text-slate-500">PDF, PNG, JPEG, TIFF, XLSX, XLS or CSV · up to {MAX_UPLOAD_MB} MB each</p>
      </div>
      {items.length > 0 ? (
        <ul className="mt-4 space-y-2" aria-label="Uploads">
          {items.map((item) => (
            <li key={item.key} className="rounded-md border border-slate-200 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 truncate font-medium">{item.name}</span>
                <span>
                  {item.state === "uploading" && `Uploading ${percent(item.progress)}`}
                  {item.state === "done" && item.documentId && (
                    <Link className="btn-link" href={`/documents/${item.documentId}`}>Uploaded · open</Link>
                  )}
                  {item.state === "duplicate" && item.documentId && (
                    <Link className="btn-link" href={`/documents/${item.documentId}`}>Already uploaded · open existing</Link>
                  )}
                  {item.state === "error" && <span className="text-red-700">{item.message}</span>}
                </span>
              </div>
              {item.state === "uploading" ? (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200" role="progressbar"
                  aria-label={`Upload progress for ${item.name}`} aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={Math.round(item.progress * 100)}>
                  <div className="h-full bg-blue-600 transition-all" style={{ width: percent(item.progress) }} />
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
