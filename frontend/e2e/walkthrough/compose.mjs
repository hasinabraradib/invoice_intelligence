// Builds the final walkthrough from the recorded scenes (record.mjs):
//  1. walkthrough.srt from the measured cue times (for uploading as captions);
//  2. one PNG per subtitle: white text on a semi-transparent dark box, about 40 px;
//  3. ffmpeg: trim each scene to its title card, join them, and burn the subtitles in with
//     the overlay filter (bottom of the frame, or the top when the highlighted element is
//     low on screen). H.264, preset veryfast, CRF 23, no audio track.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { VIEW, wrapSubtitle } from "./lib.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "../../../walkthrough-output");
const SUBS = path.join(OUT, "subs");
const MARGIN = 56;

const duration = (file) =>
  Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]).toString());

function srtTime(seconds) {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)},${pad(ms % 1000, 3)}`;
}

async function main() {
  const scenes = JSON.parse(fs.readFileSync(path.join(OUT, "cues.json"), "utf8"));
  // Place every cue on the final timeline: each scene starts at its title card.
  let offset = 0;
  const cues = [];
  for (const scene of scenes) {
    scene.length = duration(scene.file) - scene.trimStart;
    for (const cue of scene.cues) {
      cues.push({ ...cue, start: offset + cue.start - scene.trimStart, end: offset + cue.end - scene.trimStart });
    }
    offset += scene.length;
  }

  fs.writeFileSync(
    path.join(OUT, "walkthrough.srt"),
    cues.map((c, i) => `${i + 1}\n${srtTime(c.start)} --> ${srtTime(c.end)}\n${wrapSubtitle(c.text).join("\n")}\n`).join("\n"),
  );

  // Subtitle images, rendered by the browser with the system sans-serif font.
  fs.rmSync(SUBS, { recursive: true, force: true });
  fs.mkdirSync(SUBS, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEW });
  for (const [i, cue] of cues.entries()) {
    const lines = wrapSubtitle(cue.text).map((l) => l.replace(/&/g, "&amp;").replace(/</g, "&lt;"));
    await page.setContent(`<html><body style="margin:0;background:transparent">
      <div id="s" style="display:inline-block;max-width:1600px;padding:16px 34px 18px;border-radius:16px;
        background:rgba(16,15,13,.78);color:#fff;text-align:center;
        font:600 40px/1.32 -apple-system,'SF Pro Display','Helvetica Neue',Arial,sans-serif;letter-spacing:-0.01em">
        ${lines.join("<br>")}</div></body></html>`);
    const box = await page.locator("#s").boundingBox();
    cue.png = path.join(SUBS, `${String(i + 1).padStart(3, "0")}.png`);
    cue.h = Math.round(box.height);
    await page.locator("#s").screenshot({ path: cue.png, omitBackground: true });
  }
  await browser.close();

  // One ffmpeg pass: trim + join the scenes, then overlay each subtitle in its time window.
  const args = ["-y", "-v", "error"];
  for (const scene of scenes) args.push("-ss", scene.trimStart.toFixed(3), "-i", scene.file);
  for (const cue of cues) args.push("-i", cue.png);
  const n = scenes.length;
  const parts = scenes.map((_, i) => `[${i}:v]fps=30,scale=${VIEW.width}:${VIEW.height},setsar=1,format=yuv420p[v${i}]`);
  parts.push(`${scenes.map((_, i) => `[v${i}]`).join("")}concat=n=${n}:v=1:a=0[b0]`);
  cues.forEach((cue, i) => {
    const y = cue.position === "top" ? MARGIN : VIEW.height - cue.h - MARGIN;
    parts.push(
      `[b${i}][${n + i}:v]overlay=x=(W-w)/2:y=${y}:enable='between(t,${cue.start.toFixed(3)},${cue.end.toFixed(3)})'[b${i + 1}]`,
    );
  });
  const filter = parts.join(";");
  fs.writeFileSync(path.join(OUT, "filter.txt"), filter);
  args.push(
    "-/filter_complex", path.join(OUT, "filter.txt"),
    "-map", `[b${cues.length}]`, "-an",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    path.join(OUT, "walkthrough.mp4"),
  );
  execFileSync("ffmpeg", args, { stdio: "inherit" });

  const total = duration(path.join(OUT, "walkthrough.mp4"));
  fs.writeFileSync(path.join(OUT, "timeline.json"), JSON.stringify({ total, scenes: scenes.map((s) => ({ number: s.number, title: s.title, length: s.length })), cues }, null, 1));
  console.log(`walkthrough.mp4: ${Math.floor(total / 60)} min ${Math.round(total % 60)} s, ${cues.length} subtitles`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
