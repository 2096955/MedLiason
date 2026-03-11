/**
 * Playwright rendering audit for Knowledge Graph visualization.
 *
 * Validates the SVG columnar graph renderer:
 * - SVG container renders with 4 column headers
 * - Edge legend shows QUERIED/FOUND/EVIDENCED_BY/CITED
 * - Entity filter buttons work
 * - Nodes render with data-testid attributes
 * - Light slate background (#f8fafc)
 * - NLQ query bar present
 *
 * Usage:
 *   npx playwright install chromium   # first time
 *   KG_URL=https://medexpert-v2-xxx.run.app node docs/audit_kg_rendering.mjs
 *
 * Or against local dev:
 *   node docs/audit_kg_rendering.mjs   # defaults to http://localhost:3000
 */
import { createRequire } from "module";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "client",
  "webui",
  "frontend",
);
const require = createRequire(path.join(frontendDir, "package.json"));

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (e) {
  console.error(
    `Cannot resolve 'playwright' from ${frontendDir}.\n` +
      `Run: cd client/webui/frontend && npm install\n` +
      `Error: ${e.message}`,
  );
  process.exit(1);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOTS_DIR = path.join(__dirname, "kg_audit_screenshots");
const BASE_URL = process.env.KG_URL || "http://localhost:3000";

if (!fs.existsSync(SCREENSHOTS_DIR)) {
  fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
}

const PASS = "\x1b[32mPASS\x1b[0m";
const FAIL = "\x1b[31mFAIL\x1b[0m";

async function main() {
  console.log(`\nKnowledge Graph Rendering Audit`);
  console.log(`URL: ${BASE_URL}`);
  console.log("=".repeat(60));

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);

  let passed = 0;
  let failed = 0;

  function check(name, ok, detail = "") {
    if (ok) {
      passed++;
      console.log(`  ${PASS}  ${name}${detail ? " — " + detail : ""}`);
    } else {
      failed++;
      console.log(`  ${FAIL}  ${name}${detail ? " — " + detail : ""}`);
    }
  }

  // ── Step 1: Submit a research query to populate Memgraph ──────────
  try {
    console.log("\n1. Submitting research query to populate graph...");
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(3000);

    // Find the chat input (contenteditable div or textarea)
    const chatInput = page.locator('[contenteditable="true"], textarea[placeholder*="message"], textarea[placeholder*="Ask"]').first();
    const inputExists = await chatInput.count();

    if (inputExists > 0) {
      await chatInput.click();
      await chatInput.fill("What are the treatments for type 2 diabetes?");
      // Press Enter or click send
      await chatInput.press("Enter");
      console.log("  Query submitted. Waiting for research pipeline (up to 5 min)...");

      // Wait for a response — look for the disclaimer or any substantial response
      try {
        await page.locator("text=medical advice, text=educational purposes").first().waitFor({
          state: "visible",
          timeout: 300_000, // 5 min max for full pipeline
        });
        console.log("  Research response received.");
      } catch {
        console.log("  Timeout waiting for response — checking KG anyway.");
      }
      await page.waitForTimeout(5000); // Let PERSIST step complete
      await page.screenshot({ path: path.join(SCREENSHOTS_DIR, "00-chat-response.png"), fullPage: false });
      console.log("  Screenshot: 00-chat-response.png");
    } else {
      console.log("  Could not find chat input — skipping query submission.");
    }
  } catch (e) {
    console.log(`  Query submission failed: ${e.message} — continuing to KG page.`);
  }

  // ── Step 2: Navigate to KG page and audit ───────────────────────
  try {
    console.log("\n2. Navigating to Knowledge Graph page...");
    // Try clicking the nav link first (more reliable for hash router SPAs)
    const kgNavLink = page.locator('a[href*="knowledge-graph"]').or(page.getByText("Knowledge", { exact: true }));
    if (await kgNavLink.count() > 0) {
      console.log("  Found KG nav link — clicking...");
      await kgNavLink.first().click();
      await page.waitForTimeout(5000);
    } else {
      console.log("  No KG nav link found — navigating directly...");
      // Use domcontentloaded — networkidle never fires due to SSE connections
      await page.goto(`${BASE_URL}/#/knowledge-graph`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(8000);
    }

    // Take screenshot
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, "01-kg-initial.png"), fullPage: false });
    console.log("  Screenshot: 01-kg-initial.png\n");

    // ── SVG container rendered ──────────────────────────────
    const svgCount = await page.locator("svg").count();
    check("SVG container rendered", svgCount > 0, `${svgCount} SVG element(s)`);

    // ── Column headers (SVG text elements) ────────────────────
    const sessionsHeader = await page.locator("text=SESSIONS").count();
    check("SESSIONS column header", sessionsHeader > 0);

    const specialistsHeader = await page.locator("text=SPECIALISTS").count();
    check("SPECIALISTS column header", specialistsHeader > 0);

    const entitiesHeader = await page.locator("text=SHARED ENTITIES").count();
    check("SHARED ENTITIES column header", entitiesHeader > 0);

    const studiesHeader = await page.locator("text=STUDIES").count();
    check("STUDIES column header", studiesHeader > 0);

    // ── Edge legend ──────────────────────────────────────────
    const queriedLegend = await page.locator("text=QUERIED").count();
    check("Edge legend: QUERIED", queriedLegend > 0);

    const foundLegend = await page.locator("text=FOUND").count();
    check("Edge legend: FOUND", foundLegend > 0);

    const evidencedLegend = await page.locator("text=EVIDENCED_BY").count();
    check("Edge legend: EVIDENCED_BY", evidencedLegend > 0);

    const citedLegend = await page.locator("text=CITED").count();
    check("Edge legend: CITED", citedLegend > 0);

    // ── Light slate background ────────────────────────────────
    const bgColor = await page.locator('[style*="background: rgb(248"]').count();
    check("Light slate background (#f8fafc)", bgColor > 0);

    // ── Header elements ───────────────────────────────────────
    const headerTitle = await page.locator("text=Knowledge Graph").first().count();
    check("Page title visible", headerTitle > 0);

    const refreshBtn = await page.locator("text=Refresh").count();
    check("Refresh button visible", refreshBtn > 0);

    // ── Unified mode (no tabs) ───────────────────────────────
    // The old Session/Knowledge Base tabs should be gone
    const sessionTab = await page.locator("button:has-text('Session')").count();
    const kbTab = await page.locator("button:has-text('Knowledge Base')").count();
    check("No session/explore tabs (unified mode)", sessionTab === 0 && kbTab === 0);

    // ── Entity filter buttons ────────────────────────────────
    const diseaseFilter = await page.locator("button:has-text('Disease')").count();
    check("Disease filter button", diseaseFilter > 0);

    const drugFilter = await page.locator("button:has-text('Drug')").count();
    check("Drug filter button", drugFilter > 0);

    const geneFilter = await page.locator("button:has-text('Gene')").count();
    check("Gene filter button", geneFilter > 0);

    const allFilter = await page.locator("button:has-text('All')").count();
    check("All filter button", allFilter > 0);

    // ── Click Drug filter ─────────────────────────────────────
    try {
      await page.locator("button:has-text('Drug')").first().click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: path.join(SCREENSHOTS_DIR, "02-kg-drug-filter.png"), fullPage: false });
      console.log("\n  Screenshot: 02-kg-drug-filter.png");
      check("Drug filter interaction", true);
    } catch (e) {
      check("Drug filter interaction", false, e.message);
    }

    // ── Check for graph nodes or empty state ──────────────────
    const graphNodes = await page.locator("[data-testid^='graph-node-']").count();
    const emptyState = await page.locator("text=No graph data available").count();
    check("Graph nodes or empty state", graphNodes > 0 || emptyState > 0,
      graphNodes > 0 ? `${graphNodes} nodes` : "empty state shown");

    // ── If nodes exist, check edges ───────────────────────────
    if (graphNodes > 0) {
      const edgePaths = await page.locator("svg path[d^='M']").count();
      check("Edge paths rendered", edgePaths > 0, `${edgePaths} path(s)`);

      await page.screenshot({ path: path.join(SCREENSHOTS_DIR, "03-kg-with-data.png"), fullPage: false });
      console.log("  Screenshot: 03-kg-with-data.png");
    }

    // ── NLQ query bar ─────────────────────────────────────────
    const queryBar = await page.locator('input[placeholder*="knowledge graph"], input[placeholder*="Ask"]').count();
    check("NLQ query bar visible", queryBar > 0);

  } catch (e) {
    check("Page load", false, e.message);
  }

  // Final screenshot
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, "04-kg-final.png"), fullPage: false });
  console.log("  Screenshot: 04-kg-final.png");

  await browser.close();

  console.log("\n" + "=".repeat(60));
  console.log(`Results: ${passed} passed, ${failed} failed`);
  console.log(`Screenshots saved to: ${SCREENSHOTS_DIR}`);
  console.log("=".repeat(60));

  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error("KG rendering audit failed:", err);
  process.exit(1);
});
