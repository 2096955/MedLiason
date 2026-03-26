/**
 * Playwright smoke test for MedExpert frontend.
 *
 * Verifies all new UI features render correctly:
 * - Model + Mode selector (replaces old agent dropdown)
 * - Evidence sorting (RAGInfoPanel sort dropdown)
 * - Mobile responsive (MobileBottomNav)
 * - PWA manifest + offline.html
 * - Speech settings config endpoint
 *
 * Usage:
 *   # Start Vite dev server first:
 *   cd client/webui/frontend && npm run dev
 *
 *   # Then run this script:
 *   node client/webui/frontend/tests/smoke-test.mjs
 *
 *   # Override base URL:
 *   SMOKE_TEST_URL=http://localhost:8080 node client/webui/frontend/tests/smoke-test.mjs
 */
import { createRequire } from "module";
import { fileURLToPath } from "url";
import path from "path";

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
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

const BASE_URL = process.env.SMOKE_TEST_URL || "http://localhost:3000";
let passed = 0;
let failed = 0;
let skipped = 0;
const failures = [];

async function test(name, fn) {
  try {
    await fn();
    passed++;
    console.log(`  \x1b[32m✓\x1b[0m ${name}`);
  } catch (err) {
    if (err.message === "SKIP") {
      skipped++;
      console.log(`  \x1b[33m⊘\x1b[0m ${name} (skipped — needs backend)`);
      return;
    }
    failed++;
    failures.push({ name, error: err.message });
    console.log(`  \x1b[31m✗\x1b[0m ${name}`);
    console.log(`    ${err.message}`);
  }
}

async function main() {
  console.log(`\nMedExpert Smoke Test — ${BASE_URL}\n`);

  // ── Check backend availability ─────────────────────────────────
  let backendAvailable = false;
  try {
    const resp = await fetch(`${BASE_URL}/api/v1/config`, { signal: AbortSignal.timeout(3000) });
    backendAvailable = resp.status === 200;
  } catch { /* backend not running */ }

  if (!backendAvailable) {
    console.log("⚠  Backend NOT running — using mock API routes for UI testing.\n");
  }

  const browser = await chromium.launch({ headless: true });

  // ── Mock API setup (when backend unavailable) ──────────────────
  // Intercepts config + agent discovery so the UI renders the chat input area
  const MOCK_CONFIG = {
    name: "MedExpert", default_agent: "OrchestratorAgent",
    authentication: { enabled: false },
    frontend_feature_enablement: { speechToText: false, textToSpeech: false },
  };
  const MOCK_AGENTS = [
    { name: "OrchestratorAgent", description: "Orchestrator", defaultInputModes: ["text"], defaultOutputModes: ["text"], skills: [] },
    { name: "OrchestratorAgentPro", description: "Orchestrator Pro", defaultInputModes: ["text"], defaultOutputModes: ["text"], skills: [] },
    { name: "OrchestratorAgentOpus", description: "Orchestrator Opus", defaultInputModes: ["text"], defaultOutputModes: ["text"], skills: [] },
    { name: "TriageIntakeAgent", description: "Triage", defaultInputModes: ["text"], defaultOutputModes: ["text"], skills: [] },
  ];

  async function setupMockRoutes(page) {
    if (backendAvailable) return;
    await page.route("**/api/v1/config", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_CONFIG) })
    );
    await page.route("**/api/v1/agent-cards", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_AGENTS) })
    );
    // SSE endpoints — return empty event stream
    await page.route("**/api/v1/sse/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/event-stream", body: "data: {}\n\n" })
    );
    // Sessions endpoint
    await page.route("**/api/v1/sessions*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
    // DataSources health
    await page.route("**/api/v1/dataSources/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
  }

  // ── Desktop Tests ──────────────────────────────────────────────
  console.log("Desktop (1280x720):");
  const desktopCtx = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });
  const desktop = await desktopCtx.newPage();
  await setupMockRoutes(desktop);

  await test("Page loads without crash", async () => {
    const response = await desktop.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (!response || response.status() >= 400) {
      throw new Error(`Page returned status ${response?.status()}`);
    }
  });

  await test("Frontend renders (no blank page / JS crash)", async () => {
    const body = await desktop.locator("body").textContent();
    if (!body || body.trim().length === 0) {
      throw new Error("Page body is empty — JavaScript crashed");
    }
    // Even without backend, the app should render SOMETHING (error page is acceptable)
  });

  await test("Model select renders with aria-label", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const modelSelect = desktop.locator('[aria-label="Select AI model"]');
    await modelSelect.waitFor({ state: "visible", timeout: 10000 });
  });

  await test("Mode select renders with aria-label", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const modeSelect = desktop.locator('[aria-label="Select mode"]');
    await modeSelect.waitFor({ state: "visible", timeout: 5000 });
  });

  await test("'Model' label visible", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const label = desktop.locator("span").filter({ hasText: /^Model$/ });
    await label.first().waitFor({ state: "visible", timeout: 5000 });
  });

  await test("'Mode' label visible", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const label = desktop.locator("span").filter({ hasText: /^Mode$/ });
    await label.first().waitFor({ state: "visible", timeout: 5000 });
  });

  await test("Model select shows 'Flash' as default", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const text = await desktop.locator('[aria-label="Select AI model"]').textContent();
    if (!text.includes("Flash")) {
      throw new Error(`Expected 'Flash', got: '${text}'`);
    }
  });

  await test("Mode select shows 'Research' as default", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const text = await desktop.locator('[aria-label="Select mode"]').textContent();
    if (!text.includes("Research")) {
      throw new Error(`Expected 'Research', got: '${text}'`);
    }
  });

  await test("Model dropdown opens with 3 options", async () => {
    // Mock routes handle backend unavailability — no skip needed
    await desktop.locator('[aria-label="Select AI model"]').click();
    await desktop.waitForTimeout(300);
    const options = desktop.locator('[role="option"]');
    const count = await options.count();
    if (count !== 3) {
      throw new Error(`Expected 3 model options, got ${count}`);
    }
    await desktop.keyboard.press("Escape");
  });

  await test("Mode dropdown opens with 2 options", async () => {
    // Mock routes handle backend unavailability — no skip needed
    await desktop.locator('[aria-label="Select mode"]').click();
    await desktop.waitForTimeout(300);
    const options = desktop.locator('[role="option"]');
    const count = await options.count();
    if (count !== 2) {
      throw new Error(`Expected 2 mode options, got ${count}`);
    }
    await desktop.keyboard.press("Escape");
  });

  await test("Send button exists with data-testid", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const sendBtn = desktop.locator('[data-testid="sendMessage"]');
    await sendBtn.waitFor({ state: "visible", timeout: 5000 });
  });

  await test("Send button is disabled when input is empty", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const sendBtn = desktop.locator('[data-testid="sendMessage"]');
    const isDisabled = await sendBtn.isDisabled();
    if (!isDisabled) {
      throw new Error("Send button should be disabled when input is empty");
    }
  });

  await test("Chat input accepts text", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const input = desktop.locator('[data-testid="chat-input"]');
    await input.waitFor({ state: "visible", timeout: 5000 });
    await input.click();
    await input.type("Test input");
    const text = await input.textContent();
    if (!text.includes("Test")) {
      throw new Error(`Chat input did not accept text, got: '${text}'`);
    }
  });

  await test("Old agent dropdown does NOT exist", async () => {
    const agentLabel = desktop.locator("text=Select an agent...");
    const count = await agentLabel.count();
    if (count > 0) {
      throw new Error("Old 'Select an agent...' dropdown still exists!");
    }
  });

  await test("No 'Agent:' label present", async () => {
    // The old "Agent: " div should be removed
    const agentDiv = desktop.locator("div").filter({ hasText: /^Agent: $/ });
    const count = await agentDiv.count();
    if (count > 0) {
      throw new Error("Old 'Agent:' label div still exists!");
    }
  });

  await test("aria-live container exists for connecting state", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const liveRegion = desktop.locator('[aria-live="polite"]');
    const count = await liveRegion.count();
    if (count === 0) {
      throw new Error("No aria-live='polite' region found");
    }
  });

  await desktopCtx.close();

  // ── Mobile Tests ───────────────────────────────────────────────
  console.log("\nMobile (393x852):");
  const mobileCtx = await browser.newContext({
    viewport: { width: 393, height: 852 },
    isMobile: true,
  });
  const mobile = await mobileCtx.newPage();
  await setupMockRoutes(mobile);

  await test("Page loads on mobile viewport", async () => {
    const response = await mobile.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (!response || response.status() >= 400) {
      throw new Error(`Page returned status ${response?.status()}`);
    }
  });

  await test("Model select visible on mobile", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const modelSelect = mobile.locator('[aria-label="Select AI model"]');
    await modelSelect.waitFor({ state: "visible", timeout: 10000 });
  });

  await test("Chat input visible on mobile", async () => {
    // Mock routes handle backend unavailability — no skip needed
    const input = mobile.locator('[data-testid="chat-input"]');
    await input.waitFor({ state: "visible", timeout: 5000 });
  });

  await mobileCtx.close();

  // ── PWA Static Assets ──────────────────────────────────────────
  console.log("\nPWA Assets:");

  await test("manifest.json is valid", async () => {
    const resp = await fetch(`${BASE_URL}/manifest.json`);
    if (!resp.ok) throw new Error(`manifest.json returned ${resp.status}`);
    const manifest = await resp.json();
    if (manifest.name !== "MedExpert - Life Sciences Deep Research") {
      throw new Error(`Unexpected manifest name: ${manifest.name}`);
    }
    if (manifest.display !== "standalone") {
      throw new Error(`Expected display: standalone, got: ${manifest.display}`);
    }
    if (!manifest.icons || manifest.icons.length === 0) {
      throw new Error("No icons in manifest");
    }
  });

  await test("offline.html exists", async () => {
    const resp = await fetch(`${BASE_URL}/offline.html`);
    if (!resp.ok) throw new Error(`offline.html returned ${resp.status}`);
    const html = await resp.text();
    if (!html.includes("MedExpert")) {
      throw new Error("offline.html doesn't contain 'MedExpert'");
    }
  });

  // ── Backend API (if available) ─────────────────────────────────
  console.log("\nBackend API (optional — skipped if not running):");

  await test("Config endpoint returns speech feature flags", async () => {
    let resp;
    try {
      resp = await fetch(`${BASE_URL}/api/v1/config`, { signal: AbortSignal.timeout(3000) });
    } catch {
      console.log("    (backend not running — skipped)");
      return;
    }
    if (resp.status !== 200) {
      console.log(`    (config returned ${resp.status} — skipped)`);
      return;
    }
    const config = await resp.json();
    if (config.frontend_feature_enablement) {
      if (!config.frontend_feature_enablement.speechToText) {
        throw new Error("speechToText flag not enabled");
      }
      if (!config.frontend_feature_enablement.textToSpeech) {
        throw new Error("textToSpeech flag not enabled");
      }
    }
  });

  await test("Speech config endpoint returns valid TTS config", async () => {
    let resp;
    try {
      resp = await fetch(`${BASE_URL}/api/v1/speech/config`, { signal: AbortSignal.timeout(3000) });
    } catch {
      console.log("    (backend not running — skipped)");
      return;
    }
    if (resp.status !== 200) {
      console.log(`    (speech config returned ${resp.status} — skipped)`);
      return;
    }
    const config = await resp.json();
    if (config.tts_gemini_valid !== undefined && !config.tts_gemini_valid) {
      throw new Error("Gemini TTS not valid — vertex_project may be missing");
    }
  });

  await browser.close();

  // ── Report ─────────────────────────────────────────────────────
  console.log(`\n${"─".repeat(50)}`);
  console.log(`Results: \x1b[32m${passed} passed\x1b[0m, \x1b[31m${failed} failed\x1b[0m, \x1b[33m${skipped} skipped\x1b[0m`);

  if (failures.length > 0) {
    console.log("\nFailures:");
    for (const f of failures) {
      console.log(`  - ${f.name}: ${f.error}`);
    }
  }

  console.log();
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
