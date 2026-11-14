import path from "node:path";
import { describe, expect, it } from "vitest";
import { readScript, readingSeconds, wrapSubtitle } from "../../e2e/walkthrough/lib.mjs";

type Scene = { number: number; title: string; budget: number; cues: { id: string; text: string }[] };

describe("walkthrough script", () => {
  const scenes: Scene[] = readScript(path.resolve(__dirname, "../../../docs/walkthrough/script.md"));

  it("has the nine scenes, in order, with cues", () => {
    expect(scenes.map((s) => s.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    for (const scene of scenes) expect(scene.cues.length).toBeGreaterThan(2);
  });

  it("keeps cue ids unique and every subtitle within two lines of about 12 words", () => {
    const ids = scenes.flatMap((s) => s.cues.map((c) => c.id));
    expect(new Set(ids).size).toBe(ids.length);
    for (const cue of scenes.flatMap((s) => s.cues)) {
      const lines: string[] = wrapSubtitle(cue.text);
      expect(lines.length).toBeLessThanOrEqual(2);
      for (const line of lines) expect(line.split(" ").length).toBeLessThanOrEqual(12);
    }
  });

  it("plans 5 to 7 minutes of reading time plus title cards", () => {
    const reading = scenes.flatMap((s) => s.cues).reduce((sum, c) => sum + readingSeconds(c.text), 0);
    const budget = scenes.reduce((sum, s) => sum + s.budget, 0);
    expect(budget).toBeGreaterThanOrEqual(300);
    expect(budget).toBeLessThanOrEqual(420);
    expect(reading + scenes.length * 1.5).toBeLessThan(budget);
  });
});

describe("subtitle timing and wrapping", () => {
  it("gives 2.5 words per second, at least 2 seconds", () => {
    expect(readingSeconds("I confirm it.")).toBe(2);
    expect(readingSeconds("one two three four five six seven eight nine ten")).toBe(4);
  });

  it("keeps short lines whole and splits long ones evenly, preferring punctuation", () => {
    expect(wrapSubtitle("Short and sweet.")).toEqual(["Short and sweet."]);
    expect(wrapSubtitle("It only proposes the change. Nothing happens until I confirm.")).toEqual([
      "It only proposes the change.",
      "Nothing happens until I confirm.",
    ]);
  });
});
