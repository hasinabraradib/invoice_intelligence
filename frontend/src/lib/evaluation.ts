// Headline numbers of the latest `make eval` run, formatted for the dashboard (pure).
import type { components } from "./api-schema";

export type EvalRunView = components["schemas"]["EvalRunView"];
export type EvalTile = { label: string; value: string; hint: string };

const DASH = "–";
const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : DASH);
const secs = (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)} s` : DASH);
const usd = (v: unknown) =>
  typeof v === "number" ? `$${v < 0.01 ? v.toFixed(5) : v.toFixed(3)}` : DASH;

/** The tiles shown, in order. Missing numbers show as a dash rather than 0. */
export function evaluationTiles(headline: Record<string, unknown>): EvalTile[] {
  return [
    { label: "Field accuracy", value: pct(headline.field_accuracy), hint: "fields right, incl. correctly empty" },
    { label: "False fills", value: pct(headline.false_fill_rate), hint: "values given where none was printed" },
    { label: "Line items F1", value: pct(headline.line_item_f1), hint: "description + exact line total" },
    { label: "Flag precision", value: pct(headline.flag_precision), hint: "raised flags that were right" },
    { label: "Flag recall", value: pct(headline.flag_recall), hint: "planted problems that were flagged" },
    { label: "Documents processed", value: pct(headline.document_success_rate), hint: "not failed" },
    { label: "Time per document", value: secs(headline.p50_seconds_per_document), hint: "median (p50)" },
    { label: "Cost per document", value: usd(headline.cost_usd_per_document), hint: "estimated from tokens" },
  ];
}
