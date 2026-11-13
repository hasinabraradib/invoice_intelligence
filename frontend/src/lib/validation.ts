// Input checks. Money stays a decimal STRING end to end (never a JS number), so values
// like 0.1 + 0.2 can never pick up float rounding errors.

export type Check = { ok: true; value: string | null } | { ok: false; error: string };

const MONEY = /^-?\d{1,16}(\.\d{1,2})?$/; // backend: NUMERIC(18,2)
const QUANTITY = /^-?\d{1,14}(\.\d{1,4})?$/; // backend: NUMERIC(18,4)

function decimal(input: string, pattern: RegExp, places: number): Check {
  const text = input.trim().replace(/,/g, ""); // allow "1,320.00"
  if (text === "") return { ok: true, value: null };
  if (!pattern.test(text)) {
    return { ok: false, error: `Use a number with up to ${places} decimals, e.g. 1320.${"0".repeat(places)}` };
  }
  return { ok: true, value: text };
}

export const checkMoney = (input: string) => decimal(input, MONEY, 2);
export const checkQuantity = (input: string) => decimal(input, QUANTITY, 4);

/** ISO date YYYY-MM-DD that really exists (rejects 2026-02-30). */
export function checkDate(input: string): Check {
  const text = input.trim();
  if (text === "") return { ok: true, value: null };
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return { ok: false, error: "Use the format YYYY-MM-DD." };
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  const real =
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
  return real ? { ok: true, value: text } : { ok: false, error: "That date does not exist." };
}

export function checkText(input: string, max = 500): Check {
  const text = input.trim();
  if (text.length > max) return { ok: false, error: `At most ${max} characters.` };
  return { ok: true, value: text === "" ? null : text };
}

/** Approval rule (mirrors the backend): a reviewer name is always needed, and when error
 * issues remain an override reason is required too. */
export function canApprove(errorCount: number, reviewer: string, reason: string): boolean {
  if (reviewer.trim() === "") return false;
  return errorCount === 0 || reason.trim() !== "";
}

export const ACCEPTED_EXTENSIONS = [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".xlsx", ".xls", ".csv"];
export const MAX_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB ?? 20);

/** Quick client-side check before uploading; the server still checks the real bytes. */
export function checkUploadFile(file: { name: string; size: number }): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    return "Unsupported type. Use PDF, PNG, JPEG, TIFF, XLSX, XLS or CSV.";
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) return `Larger than ${MAX_UPLOAD_MB} MB.`;
  return null;
}
