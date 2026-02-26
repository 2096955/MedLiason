/**
 * Playwright script to capture MedExpert UI screenshots for the README.
 *
 * Prerequisites (run each in its own terminal):
 *   Terminal 1 — Gateway:    cd medexpert && sam run configs/gateways/webui.yaml
 *   Terminal 2 — Frontend:   cd client/webui/frontend && npm run dev
 *   Terminal 3 — Chromium:   npx playwright install chromium   (first time only)
 *
 * Wait until both services are accepting connections, then:
 *   node docs/take_screenshots.mjs
 *
 * Override the base URL via:
 *   SCREENSHOT_BASE_URL=http://localhost:8000 node docs/take_screenshots.mjs
 *
 * The script uses stable DOM selectors (data-testid, aria-label) sourced from
 * the actual component implementations.  Each section is wrapped in try/catch
 * so a single failure doesn't abort the remaining captures.
 */
import { createRequire } from "module";
import { fileURLToPath } from "url";
import path from "path";

// ── Issue E guard: resolve playwright with an actionable error ──────
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
      `Ensure you've run:  cd client/webui/frontend && npm install\n` +
      `Original error: ${e.message}`,
  );
  process.exit(1);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const BASE_URL = process.env.SCREENSHOT_BASE_URL || "http://localhost:3000";

// Small buffer after condition-based waits to let animations settle.
const SETTLE_MS = 500;

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);

  let failures = 0;

  /** Take a viewport screenshot. */
  async function screenshot(name) {
    const filePath = path.join(SCREENSHOTS_DIR, `${name}.png`);
    await page.screenshot({ path: filePath, fullPage: false });
    console.log(`  ✓ ${name}.png`);
  }

  /** Log a section failure, increment counter, continue. */
  function fail(section, selector, err) {
    failures++;
    console.error(
      `  ✗ [${section}] Selector failed: ${selector}\n` +
        `    ${err.message}\n` +
        `    Continuing to next section…`,
    );
  }

  // ─── 1. Chat page — welcome state, side panel collapsed ───────────
  try {
    console.log("📸 1/6  Chat page (dark, collapsed panel)…");
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    // Wait for the side-panel toggle to appear — it's always present on
    // the chat page and confirms React has fully rendered the layout.
    // (The chat input is a contenteditable div without a native placeholder
    // attribute, so we can't use [placeholder*=] as a ready-check.)
    await page.getByTestId("expandPanel").waitFor({ state: "visible" });
    await page.waitForTimeout(SETTLE_MS);
    await screenshot("01-chat-welcome");
  } catch (e) {
    fail("Chat welcome", 'getByTestId("expandPanel") readiness check', e);
  }

  // ─── 2. Expand side panel + capture each tab ──────────────────────
  try {
    console.log("📸 2/6  Side panel tabs…");

    // Expand
    await page.getByTestId("expandPanel").click();
    // Confirm panel expanded: the "files" tab trigger must become visible
    await page.getByTestId("files").waitFor({ state: "visible" });
    await page.waitForTimeout(SETTLE_MS);

    const tabs = [
      { testId: "files", name: "02-side-panel-files" },
      { testId: "activity", name: "03-side-panel-activity" },
      { testId: "datasources", name: "04-side-panel-data" },
      { testId: "memory", name: "05-side-panel-memory" },
      { testId: "performance", name: "06-side-panel-perf" },
    ];

    for (const { testId, name } of tabs) {
      try {
        console.log(`       Tab: ${testId}…`);
        await page.getByTestId(testId).click();
        // Verify the clicked tab became active (Radix UI sets data-state)
        await page
          .locator(`[data-testid="${testId}"][data-state="active"]`)
          .waitFor({ state: "attached" });
        await page.waitForTimeout(SETTLE_MS);
        await screenshot(name);
      } catch (tabErr) {
        fail(`Tab ${testId}`, `getByTestId("${testId}")`, tabErr);
      }
    }
  } catch (e) {
    fail("Side panel expand", 'getByTestId("expandPanel")', e);
  }

  // ─── 3. Agent Mesh page ───────────────────────────────────────────
  try {
    console.log('📸 3/6  Agent Mesh…');
    await page.locator('button[aria-label="Agent Mesh"]').click();
    // Verify navigation: URL should contain /agents, or heading visible
    await page.waitForURL("**/agents**", { timeout: 10_000 });
    await page.waitForTimeout(SETTLE_MS);
    await screenshot("07-agent-mesh");
  } catch (e) {
    fail("Agent Mesh", 'button[aria-label="Agent Mesh"]', e);
  }

  // ─── 4. Projects page ────────────────────────────────────────────
  try {
    console.log('📸 4/6  Projects…');
    await page.locator('button[aria-label="Projects"]').click();
    await page.waitForURL("**/projects**", { timeout: 10_000 });
    await page.waitForTimeout(SETTLE_MS);
    await screenshot("08-projects");
  } catch (e) {
    fail("Projects", 'button[aria-label="Projects"]', e);
  }

  // ─── 5. Prompts page ─────────────────────────────────────────────
  try {
    console.log('📸 5/6  Prompts…');
    await page.locator('button[aria-label="Prompts"]').click();
    await page.waitForURL("**/prompts**", { timeout: 10_000 });
    await page.waitForTimeout(SETTLE_MS);
    await screenshot("09-prompts");
  } catch (e) {
    fail("Prompts", 'button[aria-label="Prompts"]', e);
  }

  // ─── 6. Light-mode chat — fresh load with localStorage preset ─────
  try {
    console.log("📸 6/6  Light mode…");
    // ThemeProvider reads localStorage("sam-theme") on mount and calls
    // applyThemeToDOM() which sets 100+ CSS variables.  Set the key
    // BEFORE the full page reload so ThemeProvider picks it up on init.
    await page.evaluate(() => localStorage.setItem("sam-theme", "light"));
    await page.goto(BASE_URL, { waitUntil: "domcontentloaded" });
    // Confirm light class on <html>
    await page.waitForFunction(
      () => document.documentElement.classList.contains("light"),
      { timeout: 10_000 },
    );
    await page.waitForTimeout(SETTLE_MS);
    await screenshot("10-chat-light-mode");
  } catch (e) {
    fail("Light mode", "localStorage + page reload", e);
  }

  // ─── Done ─────────────────────────────────────────────────────────
  await browser.close();

  if (failures > 0) {
    console.error(`\n❌ ${failures} section(s) failed. Review warnings above.`);
    process.exit(1);
  }
  console.log("\n✅ All screenshots saved to docs/screenshots/");
}

main().catch((err) => {
  console.error("Screenshot capture failed:", err);
  process.exit(1);
});
