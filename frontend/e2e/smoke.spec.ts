import { expect, test, type APIRequestContext } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8000";
const WEB = process.env.E2E_WEB_URL ?? "http://localhost:3000";

/** A tiny one-page text PDF built by hand (no binary fixture in git). */
function makePdf(lines: string[]): Buffer {
  const text = lines
    .map((line, i) => `BT /F1 12 Tf 50 ${780 - i * 18} Td (${line.replace(/[()\\]/g, "")}) Tj ET`)
    .join("\n");
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${text.length} >>\nstream\n${text}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(pdf.length);
    pdf += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf, "latin1");
}

async function reachable(request: APIRequestContext, url: string): Promise<boolean> {
  try {
    return (await request.get(url, { timeout: 3000 })).ok();
  } catch {
    return false;
  }
}

test("upload, review, correct, approve, search", async ({ page, request }) => {
  test.skip(
    !(await reachable(request, `${API}/health`)) || !(await reachable(request, WEB)),
    "Stack is not running: start it with `make up`.",
  );

  const token = `SMOKE${Date.now()}`; // unique: makes the file new and searchable
  const pdf = makePdf([
    "Smoke Test Supplies Ltd",
    `Invoice No: ${token}`,
    "Invoice Date: 2026-03-15",
    "Bill To: Example Traders",
    "Widgets 2 x 50.00 = 100.00",
    "Total: 100.00 USD",
  ]);

  // Upload through the UI.
  await page.goto("/");
  await page.setInputFiles("#file-input", { name: `${token}.pdf`, mimeType: "application/pdf", buffer: pdf });
  const open = page.getByRole("link", { name: /Uploaded · open/ });
  await expect(open).toBeVisible({ timeout: 30_000 });
  const href = (await open.getAttribute("href")) ?? "";
  const documentId = href.split("/").pop() ?? "";

  // Wait for the worker.
  let status = "";
  let error = "";
  for (let i = 0; i < 90 && !["processed", "needs_review", "failed"].includes(status); i++) {
    const body = await (await request.get(`${API}/documents/${documentId}/status`)).json();
    status = body.status;
    error = body.error ?? "";
    if (!["processed", "needs_review", "failed"].includes(status)) await page.waitForTimeout(2000);
  }
  test.skip(status === "failed" && /LLM_/.test(error), `No LLM configured (${error}).`);
  expect(["processed", "needs_review"]).toContain(status);

  // Review: correct the total.
  await page.goto(href);
  await page.getByLabel("Your name (for the audit trail)").fill("Smoke Tester");
  await page.getByRole("button", { name: "Edit Total" }).click();
  await page.locator("#edit-total").fill("100.00");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.locator("#field-total").getByText("corrected", { exact: true })).toBeVisible();

  // Approve (with an override reason if errors remain).
  await page.getByRole("button", { name: "Approve" }).click();
  const dialog = page.getByRole("dialog", { name: "Approve invoice" });
  const reason = dialog.getByLabel(/Override reason/);
  if (await reason.isVisible()) await reason.fill("Checked by the smoke test");
  await dialog.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approved by Smoke Tester/)).toBeVisible();

  // Find it in search.
  await page.goto(`/search?q=${token}&mode=keyword`);
  await expect(page.getByRole("link", { name: new RegExp(`${token}\\.pdf`) })).toBeVisible();
});
