// Plain-language names for validation issue codes (the API sends the codes).
const LABELS: Record<string, string> = {
  MISSING_REQUIRED: "Required field missing",
  MISSING_RECOMMENDED: "Optional field missing",
  TOTAL_MISMATCH: "Total doesn't add up",
  LINE_ITEMS_SUM_MISMATCH: "Line items don't add up",
  LINE_ARITHMETIC: "Line quantity × price mismatch",
  LINE_TOTALS_INCOMPLETE: "Unreadable line totals",
  NON_POSITIVE_TOTAL: "Total is zero or negative",
  TAX_RATE_OUT_OF_RANGE: "Unusual tax rate",
  DUE_BEFORE_INVOICE_DATE: "Due before invoice date",
  INVOICE_DATE_IN_FUTURE: "Invoice date in the future",
  AMBIGUOUS_DATE: "Ambiguous date (day/month)",
  LOW_CONFIDENCE: "Low confidence",
  DUPLICATE_INVOICE: "Duplicate invoice",
  POSSIBLE_DUPLICATE: "Possible duplicate",
  CROSS_CHECK_MISMATCH: "Cross-check mismatch",
  VISION_EXTRACTED: "Read from the page image",
  OCR_VISION_DISAGREE: "OCR and image disagree",
  BOUNDARY_CHANGED: "Invoice boundaries changed",
};

export function issueLabel(code: string | null | undefined): string {
  if (!code) return "No issue recorded";
  return LABELS[code] ?? code.toLowerCase().replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}
