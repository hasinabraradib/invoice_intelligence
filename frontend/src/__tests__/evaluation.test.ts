import { describe, expect, it } from "vitest";
import { evaluationTiles } from "@/lib/evaluation";

describe("evaluationTiles", () => {
  it("formats rates, seconds and cost", () => {
    const tiles = evaluationTiles({
      field_accuracy: 0.9312, false_fill_rate: 0.02, line_item_f1: 0.875, flag_precision: 1,
      flag_recall: 0.5, document_success_rate: 1, p50_seconds_per_document: 6.43, cost_usd_per_document: 0.00071,
    });
    const value = (label: string) => tiles.find((t) => t.label === label)?.value;
    expect(value("Field accuracy")).toBe("93.1%");
    expect(value("Time per document")).toBe("6.4 s");
    expect(value("Cost per document")).toBe("$0.00071");
  });

  it("shows the hard subset next to the clean accuracy", () => {
    const tiles = evaluationTiles({ field_accuracy: 1, hard_field_accuracy: 0.75 });
    expect(tiles.map((t) => t.label).slice(0, 2)).toEqual(["Field accuracy", "Field accuracy, hard subset"]);
    expect(tiles[1].value).toBe("75.0%");
  });

  it("shows a dash, not zero, for numbers the run did not produce", () => {
    const tiles = evaluationTiles({ field_accuracy: 0.5 });
    expect(tiles.find((t) => t.label === "Cost per document")?.value).toBe("–");
  });
});
