// Records the walkthrough video, scene by scene, from docs/walkthrough/script.md.
//
//   node e2e/walkthrough/record.mjs          (run by `make walkthrough`)
//
// Each scene is recorded in its own browser context (its own video file), so a scene that
// fails can be retried without leaving the failed attempt in the final cut. Every cue
// performs its action, then holds the screen for the subtitle's reading time while a
// highlight box marks what the subtitle talks about. Waits are on real UI or API states,
// never fixed sleeps, except the reading holds themselves. The measured cue times go to
// walkthrough-output/cues.json, from which compose.mjs builds the .srt and the final video.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { OVERLAY, VIEW, readScript, readingSeconds } from "./lib.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../../..");
const OUT = path.join(ROOT, "walkthrough-output");
const RAW = path.join(OUT, "raw");
const BASE = process.env.WALKTHROUGH_URL ?? "http://localhost:3100";
const API_DOCS = process.env.WALKTHROUGH_API_DOCS ?? "http://localhost:8100/docs";
const FILES = path.join(ROOT, "eval/dataset/files");
const ONLY = process.env.SCENES ? process.env.SCENES.split(",").map(Number) : null;
const PROCESSING_MS = 8 * 60_000; // real LLM processing can be slow; we wait on states

// The six files dropped on camera (the rest is seeded beforehand, see the Makefile).
const UPLOAD = ["inv-12.pdf", "inv-14.jpg", "harddev-04.jpg", "inv-03.xlsx", "inv-04.csv", "batch-6-invoices.pdf"];
const MIME = { pdf: "application/pdf", jpg: "image/jpeg", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", csv: "text/csv" };
const FINISHED = new Set(["processed", "needs_review", "failed", "partially_failed"]);

// ---------------------------------------------------------------- API helpers (via the web server's proxy)

async function api(p, init) {
  const r = await fetch(`${BASE}/api${p}`, init);
  if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`);
  return r.json();
}
async function docByName(name) {
  const list = await api(`/documents?limit=100`);
  return list.items.find((d) => d.filename === name);
}
async function until(what, fn, timeout = PROCESSING_MS) {
  const end = Date.now() + timeout;
  for (;;) {
    const v = await fn().catch(() => null);
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 2000));
  }
}
const finished = (name) =>
  until(`${name} to finish`, async () => {
    const d = await docByName(name);
    return d && FINISHED.has(d.status) ? d : null;
  });

// ---------------------------------------------------------------- docs pages (rendered locally)

function docsPage(name, markdownFile, { only, wide } = {}) {
  let md = fs.readFileSync(path.join(ROOT, markdownFile), "utf8");
  if (only) md = only(md);
  const base = "file://" + path.dirname(path.join(ROOT, markdownFile)) + "/";
  const file = path.join(OUT, "pages", `${name}.html`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    `<!doctype html><html><head><meta charset="utf-8"><base href="${base}">
<style>
  body { margin: 0; background: #f5efe4; color: #171512; font: 22px/1.6 system-ui, -apple-system, sans-serif; }
  main { max-width: ${wide ? 1800 : 1280}px; margin: 0 auto; padding: 56px 64px 400px; }
  h1 { font-size: 56px; letter-spacing: -0.03em; margin: 0 0 18px; } h2 { font-size: 36px; margin-top: 48px; letter-spacing: -0.02em; }
  img { max-width: 100%; border-radius: 14px; border: 1px solid #e4dccd; }
  table { border-collapse: collapse; width: 100%; font-size: 18px; background: #fffcf6; border-radius: 12px; overflow: hidden; }
  th, td { border: 1px solid #e4dccd; padding: 8px 12px; text-align: left; vertical-align: top; }
  th { background: #f0e9dc; } code { font: 0.85em ui-monospace, monospace; background: #f0e9dc; padding: 1px 6px; border-radius: 6px; }
  pre { background: #fffcf6; border: 1px solid #e4dccd; padding: 16px; border-radius: 12px; overflow: auto; font-size: 16px; }
  a { color: #171512; }
</style></head><body><main id="doc"></main>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script>document.getElementById("doc").innerHTML = marked.parse(${JSON.stringify(md)}); document.body.dataset.ready = "1";</script>
</body></html>`,
  );
  return "file://" + file;
}
const section = (heading) => (md) => {
  const start = md.indexOf(heading);
  const next = md.indexOf("\n## ", start + heading.length);
  return md.slice(start, next === -1 ? undefined : next);
};

// ---------------------------------------------------------------- the stage: cursor, highlight, cues

class Stage {
  constructor(page, scene, t0) {
    Object.assign(this, { page, scene, t0, cues: [] });
  }
  now() {
    return (Date.now() - this.t0) / 1000;
  }
  async moveTo(locator) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) return;
    await this.page.mouse.move(box.x + box.width / 2, box.y + Math.min(box.height / 2, 24), { steps: 28 });
  }
  async click(locator) {
    await locator.waitFor({ state: "visible", timeout: 30_000 });
    await this.moveTo(locator);
    await this.page.waitForTimeout(150);
    await locator.click();
  }
  async type(locator, text) {
    await this.click(locator);
    await locator.pressSequentially(text, { delay: 45 });
  }
  async scrollTo(locator, block = "center") {
    await locator.evaluate((el, b) => el.scrollIntoView({ behavior: "smooth", block: b }), block);
    await this.page.waitForTimeout(900); // let the smooth scroll settle before measuring
  }
  /** Show one subtitle: highlight the target, hold for the reading time, log the times. */
  async cue(id, target) {
    const cue = this.scene.cues.find((c) => c.id === id);
    if (!cue) throw new Error(`cue ${id} is not in the script`);
    let rect = null;
    if (target) {
      await target.waitFor({ state: "visible", timeout: 60_000 });
      rect = await target.boundingBox();
      if (rect) await this.page.evaluate((r) => window.__wt?.highlight(r), rect);
    }
    // Keep the subtitle box off the highlighted element: move it to the top if needed.
    const position = rect && rect.y + rect.height > VIEW.height - 230 && rect.y > 260 ? "top" : "bottom";
    const start = this.now();
    await this.page.waitForTimeout(readingSeconds(cue.text) * 1000);
    this.cues.push({ id, text: cue.text, start, end: this.now(), position });
    await this.page.evaluate(() => window.__wt?.clearHighlight());
    await this.page.waitForTimeout(250);
  }
}

// ---------------------------------------------------------------- scenes

const SCENES = {
  async 1(s) {
    const { page } = s;
    await page.goto(docsPage("readme", "README.md", { only: (md) => md.slice(0, md.indexOf("**Contents:**")) }));
    await page.waitForSelector("body[data-ready]");
    await page.locator("img").first().waitFor();
    await s.begin();
    await s.cue("intro-problem", page.locator("h1"));
    await s.cue("intro-what", page.locator("p").first());
    await s.scrollTo(page.locator("img").first(), "start");
    await s.cue("intro-evidence", page.locator("img").first());
    await page.goto(docsPage("architecture", "docs/architecture.md", {
      only: () => "# Architecture\n\n![Components](architecture/components.png)\n",
      wide: true,
    }));
    await page.waitForSelector("body[data-ready]");
    await page.locator("img").waitFor();
    await page.evaluate(() => window.__wt.ensure());
    await s.cue("intro-arch", page.locator("img"));
  },

  async 2(s) {
    const { page } = s;
    await page.goto(`${BASE}/documents`);
    const zone = page.getByText("Drop invoices here").locator("xpath=ancestor::*[self::div or self::label][1]/..");
    await zone.waitFor();
    await s.begin();
    await s.cue("upload-page", zone);
    // Drag: a "6 files" ghost follows the cursor from the edge of the screen to the zone.
    await page.mouse.move(80, 980);
    await page.evaluate(() => window.__wt.ghost("6 files"));
    await s.moveTo(zone);
    const files = UPLOAD.map((name) => ({
      name,
      type: MIME[name.split(".").pop()],
      data: fs.readFileSync(path.join(FILES, name)).toString("base64"),
    }));
    const target = await zone.elementHandle();
    await page.evaluate(
      ([el, list]) => {
        const dt = new DataTransfer();
        for (const f of list) {
          const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
          dt.items.add(new File([bytes], f.name, { type: f.type }));
        }
        for (const type of ["dragenter", "dragover", "drop"]) {
          el.dispatchEvent(new DragEvent(type, { dataTransfer: dt, bubbles: true, cancelable: true }));
        }
      },
      [target, files],
    );
    await page.evaluate(() => window.__wt.ghost(null));
    const results = page.getByText(/Uploaded · open|Already uploaded/);
    await until("six upload results", async () => (await results.count()) >= 6, 120_000);
    const list = page.getByRole("list", { name: "Uploads" });
    await s.cue("upload-drag", list);
    await s.cue("upload-kinds", list);
    await s.cue("upload-checked", list);
    const table = page.locator("table").first();
    await s.scrollTo(table, "start");
    await s.cue("upload-progress", table);
    const progress = table.getByRole("progressbar").first();
    await progress.waitFor({ timeout: 120_000 }).catch(() => {});
    await s.cue("upload-pages", (await progress.isVisible()) ? progress.locator("xpath=ancestor::tr[1]") : table);
    await until("a dropped file to finish", async () => {
      const names = await Promise.all(UPLOAD.map(docByName));
      return names.some((d) => d && FINISHED.has(d.status));
    });
    await s.cue("upload-background", table);
  },

  async 3(s) {
    const { page } = s;
    const doc = await finished("inv-08.jpg");
    await page.goto(`${BASE}/review`);
    const row = page.getByRole("link", { name: /inv-08\.jpg/ });
    await row.waitFor();
    await s.begin();
    await s.cue("review-queue", page.locator("section[aria-label='Documents needing review']"));
    await s.click(row);
    await page.waitForURL(`**/documents/${doc.id}**`);
    const total = page.locator("#field-total");
    await total.waitFor();
    await s.cue("review-open", page.locator("h1"));
    const evidence = total.getByRole("button").filter({ hasText: /p\. \d/ }).first();
    await s.scrollTo(total);
    await s.click(evidence);
    await s.cue("review-evidence", evidence);
    await s.cue("review-jump", page.locator("section[aria-label='Document pages']"));
    await s.scrollTo(total);
    await s.cue("review-confidence", total.getByRole("meter"));
    const issue = page.locator("section[aria-labelledby='issues-heading'] li").filter({ hasText: "Expected" }).first();
    await s.scrollTo(issue);
    await s.cue("review-issue", issue);
    await s.cue("review-expected", issue.locator("strong").first().locator("xpath=.."));
    const vision = page.getByText("confirmed by a second reading of the page image").first();
    await s.scrollTo(vision);
    await s.cue("review-vision", vision.locator("xpath=ancestor::li[1]"));
    // The correction: the expected amount, as the validation computed it.
    const expected = (await issue.locator("strong").first().innerText()).replace(/[^\d.,-]/g, "").replace(/,/g, "");
    await s.scrollTo(page.locator("#reviewer-name"));
    await s.type(page.locator("#reviewer-name"), "Demo Reviewer");
    await s.scrollTo(total);
    await s.click(page.getByRole("button", { name: "Edit Total" }));
    await page.locator("#edit-total").fill("");
    await s.type(page.locator("#edit-total"), expected);
    await s.click(page.getByRole("button", { name: "Save" }));
    await total.getByText("corrected", { exact: true }).waitFor({ timeout: 60_000 });
    await s.cue("review-correct", total);
    await until("the total issue to clear", async () => (await issue.count()) === 0 || !(await issue.isVisible()), 60_000);
    const history = page.locator("section[aria-labelledby='history-heading']");
    await s.scrollTo(history);
    await s.cue("review-cleared", history);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    await page.waitForTimeout(800);
    await s.click(page.getByRole("button", { name: "Approve" }).first());
    const dialog = page.getByRole("dialog", { name: "Approve invoice" });
    await dialog.waitFor();
    const reason = dialog.getByLabel(/Override reason/);
    if (await reason.isVisible()) await s.type(reason, "Checked against the paper copy");
    await s.click(dialog.getByRole("button", { name: "Approve" }));
    const approved = page.getByText(/Approved by Demo Reviewer/);
    await approved.waitFor({ timeout: 60_000 });
    await s.cue("review-approve", approved);
  },

  async 4(s) {
    const { page } = s;
    const batch = await finished("batch-6-invoices.pdf");
    const copy = await finished("inv-06-copy.jpg");
    await page.goto(`${BASE}/documents/${batch.id}`);
    const switcher = page.locator("#invoice-select").locator("xpath=ancestor::div[contains(@class,'card')][1]");
    await switcher.waitFor();
    await s.begin();
    await s.cue("batch-open", switcher);
    await s.click(page.getByRole("button", { name: "Next invoice" }));
    await page.waitForURL(/invoice=1/).catch(() => {});
    await s.cue("batch-switch", switcher);
    await s.cue("batch-pages", page.locator("section[aria-label='Document pages']"));
    await page.goto(`${BASE}/documents/${copy.id}`);
    await page.locator("h1").waitFor();
    await page.evaluate(() => window.__wt.ensure());
    await s.cue("dup-open", page.locator("h1"));
    const dup = page.locator("section[aria-labelledby='issues-heading'] li").filter({ hasText: /duplicate/i }).first();
    await s.scrollTo(dup);
    await s.cue("dup-flag", dup);
  },

  async 5(s) {
    const { page } = s;
    await finished("batch-6-invoices.pdf");
    await finished("ledger-batch.xlsx");
    await page.goto(`${BASE}/cross-check`);
    await page.locator("#doc-a").waitFor();
    await s.begin();
    await s.cue("xc-intro", page.locator("form").first());
    await s.click(page.locator("#doc-a"));
    await page.getByRole("combobox").pressSequentially("batch", { delay: 60 });
    await page.keyboard.press("Enter");
    await s.cue("xc-pick-a", page.locator("#doc-a"));
    await s.click(page.locator("#doc-b"));
    await page.getByRole("combobox").pressSequentially("ledger", { delay: 60 });
    await page.keyboard.press("Enter");
    await s.cue("xc-pick-b", page.locator("#doc-b"));
    await s.click(page.getByRole("button", { name: /Run cross-check/ }));
    await s.cue("xc-run", page.getByRole("button", { name: /Run cross-check|Starting/ }));
    const summary = page.getByRole("list", { name: "Summary" });
    await summary.waitFor({ timeout: PROCESSING_MS });
    await s.scrollTo(summary);
    await s.cue("xc-summary", summary);
    const table = page.locator("table").first();
    await s.scrollTo(table);
    await s.cue("xc-finding", table.locator("tbody tr").first());
    const link = table.locator("tbody a").first();
    await s.moveTo(link);
    await s.cue("xc-links", link.locator("xpath=ancestor::tr[1]"));
    await s.cue("xc-nochange", table);
  },

  async 6(s) {
    const { page } = s;
    await page.goto(`${BASE}/search`);
    await page.locator("#search-q").waitFor();
    await s.begin();
    await s.type(page.locator("#search-q"), "shipping costs");
    await s.click(page.locator("label").filter({ hasText: /^Keyword/ }));
    await page.getByText("No matches").waitFor({ timeout: 60_000 });
    await s.cue("search-keyword", page.locator("form[role=search]"));
    await s.cue("search-none", page.getByText("No matches").locator("xpath=.."));
    await s.click(page.locator("label").filter({ hasText: /^Semantic/ }));
    const first = page.locator("section[aria-label='Results'] li").first();
    await first.waitFor({ timeout: 60_000 });
    await s.cue("search-semantic", page.locator("label").filter({ hasText: /^Semantic/ }));
    await s.scrollTo(first);
    await s.cue("search-found", first);
    await s.cue("search-badge", first.getByText("meaning", { exact: true }).first());
  },

  async 7(s) {
    const { page } = s;
    const csv = await finished("inv-05.csv");
    await page.goto(`${BASE}/`);
    const input = page.locator("#chat-input");
    await input.waitFor();
    await page.getByRole("heading", { level: 1 }).waitFor();
    await s.begin();
    await s.cue("chat-home", page.getByRole("heading", { level: 1 }).locator("xpath=.."));
    const send = async (text) => {
      await s.type(input, text);
      await page.keyboard.press("Enter");
      await page.getByRole("button", { name: "Stop" }).waitFor({ timeout: 30_000 }).catch(() => {});
      await page.getByRole("button", { name: "Send" }).waitFor({ timeout: 180_000 });
    };
    await send("How much did we spend with Meghna Traders?");
    const trace = page.locator("details summary").filter({ hasText: /^Used/ }).last();
    await trace.waitFor({ timeout: 60_000 });
    await s.cue("chat-ask", page.locator("p").filter({ hasText: "How much did we spend with Meghna Traders?" }).last());
    await s.click(trace);
    await s.cue("chat-tools", trace.locator("xpath=.."));
    const answer = trace.locator("xpath=ancestor::div[contains(@class,'space-y-3')][1]");
    if (!(await answer.innerText()).includes("sum_invoices")) throw new Error("the model did not use sum_invoices");
    await s.cue("chat-sum", answer);
    const sources = page.getByRole("list", { name: "Sources" }).last();
    await s.cue("chat-cite", sources);
    await send("On inv-05.csv, set the customer name to Sample Retail Ltd.");
    const card = page.getByRole("group", { name: "Proposed change" }).last();
    await card.waitFor({ timeout: 60_000 });
    await s.cue("chat-correct", page.locator("p").filter({ hasText: "set the customer name" }).last());
    await s.cue("chat-card", card);
    await s.click(card.getByRole("button", { name: "Confirm" }));
    await page.getByText("Change confirmed").last().waitFor({ timeout: 60_000 });
    await s.cue("chat-confirm", page.getByRole("group", { name: "Proposed change" }).last());
    await page.goto(`${BASE}/documents/${csv.id}`);
    const history = page.locator("section[aria-labelledby='history-heading']");
    await history.getByText(/Sample Retail Ltd/).waitFor({ timeout: 60_000 });
    await page.evaluate(() => window.__wt.ensure());
    await s.scrollTo(history);
    await s.cue("chat-audit", history);
  },

  async 8(s) {
    const { page } = s;
    await page.goto(`${BASE}/dashboard`);
    const kpis = page.locator("section[aria-label='Key numbers']");
    await kpis.waitFor();
    await page.locator("section[aria-labelledby='eval-heading']").getByText("Field accuracy, hard subset").waitFor();
    await s.begin();
    await s.cue("dash-kpis", kpis);
    await s.cue("dash-eval", page.locator("section[aria-labelledby='eval-heading']"));
    await page.goto(docsPage("evaluation", "docs/evaluation.md", { only: section("## Headline") }));
    await page.waitForSelector("body[data-ready]");
    await page.evaluate(() => window.__wt.ensure());
    const rows = page.locator("table tr");
    await s.scrollTo(rows.nth(1), "center");
    await s.cue("eval-clean", rows.nth(1));
    const hard = rows.filter({ hasText: "with vision as a second opinion" });
    await s.scrollTo(hard);
    await s.cue("eval-hard", hard);
    await page.goto(docsPage("limitations", "README.md", { only: section("## Known limitations") }));
    await page.waitForSelector("body[data-ready]");
    await page.evaluate(() => window.__wt.ensure());
    const first = page.locator("li").first();
    await s.cue("eval-limits", first);
    await s.cue("eval-review", first);
  },

  async 9(s) {
    const { page } = s;
    await page.goto(API_DOCS);
    await page.locator(".opblock").first().waitFor({ timeout: 60_000 });
    await page.evaluate(() => window.__wt.ensure());
    await s.begin();
    await s.cue("eng-docs", page.locator(".information-container, .info").first());
    const ops = page.locator(".opblock-tag-section").first();
    await s.scrollTo(ops, "start");
    await s.cue("eng-key", ops);
    await page.goto(docsPage("test-results", "docs/test-results.md"));
    await page.waitForSelector("body[data-ready]");
    await page.evaluate(() => window.__wt.ensure());
    const counts = page.locator("table").first();
    await s.cue("eng-tests", counts);
    const coverage = page.getByText(/^Overall:/).first();
    await s.scrollTo(coverage);
    await s.cue("eng-coverage", coverage);
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
    await page.waitForTimeout(800);
    await s.cue("eng-close", page.locator("h1"));
  },
};

// ---------------------------------------------------------------- run

async function recordScene(browser, scene, attempt) {
  const dir = path.join(RAW, `scene-${scene.number}-try-${attempt}`);
  fs.rmSync(dir, { recursive: true, force: true });
  const context = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: 1,
    colorScheme: "light",
    bypassCSP: true, // lets the overlay layer run; the app itself is unchanged
    recordVideo: { dir, size: VIEW },
  });
  await context.addInitScript(OVERLAY);
  const t0 = Date.now(); // the video starts when the page is created
  const page = await context.newPage();
  const stage = new Stage(page, scene, t0);
  // Title card, shown once the first view is ready; the video is trimmed to start here.
  stage.begin = async () => {
    await page.evaluate(([n, t]) => window.__wt.card(n, t), [scene.number, scene.title]);
    stage.cardAt = stage.now();
    await page.waitForTimeout(1500);
    await page.evaluate(() => window.__wt.hideCard());
    await page.waitForTimeout(400);
  };
  try {
    await SCENES[scene.number](stage);
    await page.waitForTimeout(600);
    const video = page.video();
    await context.close();
    const file = path.join(RAW, `scene-${scene.number}.webm`);
    fs.renameSync(await video.path(), file);
    fs.rmSync(dir, { recursive: true, force: true });
    return { number: scene.number, title: scene.title, file, trimStart: stage.cardAt, cues: stage.cues };
  } catch (error) {
    await page.screenshot({ path: path.join(RAW, `scene-${scene.number}-try-${attempt}-failure.png`) }).catch(() => {});
    await context.close().catch(() => {});
    fs.rmSync(dir, { recursive: true, force: true });
    throw error;
  }
}

async function main() {
  fs.mkdirSync(RAW, { recursive: true });
  const scenes = readScript(path.join(ROOT, "docs/walkthrough/script.md"));
  const browser = await chromium.launch();
  const resultsFile = path.join(OUT, "cues.json");
  const results = ONLY && fs.existsSync(resultsFile) ? JSON.parse(fs.readFileSync(resultsFile, "utf8")) : [];
  try {
    for (const scene of scenes) {
      if (ONLY && !ONLY.includes(scene.number)) continue;
      for (let attempt = 1; ; attempt++) {
        try {
          console.log(`scene ${scene.number}: ${scene.title} (try ${attempt})`);
          const result = await recordScene(browser, scene, attempt);
          const missing = scene.cues.filter((c) => !result.cues.some((r) => r.id === c.id)).map((c) => c.id);
          if (missing.length) throw new Error(`cues not shown: ${missing.join(", ")}`);
          const i = results.findIndex((r) => r.number === scene.number);
          if (i >= 0) results[i] = result;
          else results.push(result);
          results.sort((a, b) => a.number - b.number);
          fs.writeFileSync(resultsFile, JSON.stringify(results, null, 1));
          break;
        } catch (error) {
          console.log(`  failed: ${error.message.split("\n")[0]}`);
          if (attempt >= 3) throw error;
        }
      }
    }
  } finally {
    await browser.close();
  }
  console.log(`recorded ${results.length} scenes -> ${resultsFile}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
