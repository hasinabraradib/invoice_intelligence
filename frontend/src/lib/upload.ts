// Upload one file with progress events. fetch() cannot report upload progress, so this
// uses XMLHttpRequest.
import { API_URL, ApiError, type UploadResponse, type UploadResult } from "./api";

export function uploadFile(
  file: File,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("files", file);
    xhr.open("POST", `${API_URL}/documents`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onerror = () =>
      reject(new ApiError(0, "network_error", "Upload failed: cannot reach the API."));
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // handled below
      }
      const results = (body as UploadResponse | null)?.results;
      if (xhr.status < 300 && results?.[0]) {
        resolve(results[0]);
        return;
      }
      const error = (body as { error?: { code?: string; message?: string } } | null)?.error;
      reject(
        new ApiError(
          xhr.status,
          error?.code ?? "upload_failed",
          error?.message ?? `Upload failed (HTTP ${xhr.status}).`,
        ),
      );
    };
    xhr.send(form);
  });
}
