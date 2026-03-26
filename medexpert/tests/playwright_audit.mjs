/**
 * MedExpert v2 — Full Playwright Audit
 *
 * Checks:
 * 1. Page load, HTTP status, console errors
 * 2. Core layout (chat input, navigation, sidebar)
 * 3. Model selector (multi-model feature)
 * 4. Side panel tabs (Files, Activity, Data Sources, Memory, Perf)
 * 5. DataSources panel (MCP server health)
 * 6. Knowledge Graph page (hash route: /#/knowledge-graph)
 * 7. Agent Configs page (hash route: /#/agents)
 * 8. Chat message send + SSE response
 * 9. Triage mode availability (inside Select dropdown)
 * 10. API health + datasources + graph + evolution endpoints
 * 11. Theme/CSS, network errors, accessibility basics
 */

import { chromium } from 'playwright';

const BASE_URL = process.env.BASE_URL || 'https://medexpert-v2-534348290993.us-central1.run.app';
const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR || '/tmp/playwright-audit';

async function run() {
    console.log(`=== MedExpert v2 Full Audit ===`);
    console.log(`Target: ${BASE_URL}\n`);

    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();

    // Ensure screenshot dir exists
    const { mkdirSync } = await import('fs');
    try { mkdirSync(SCREENSHOT_DIR, { recursive: true }); } catch {}

    // Collect console errors
    const consoleErrors = [];
    const consoleWarnings = [];
    page.on('console', msg => {
        if (msg.type() === 'error') consoleErrors.push(msg.text());
        if (msg.type() === 'warning') consoleWarnings.push(msg.text());
    });

    // Collect network failures
    const networkErrors = [];
    page.on('requestfailed', req => {
        networkErrors.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText}`);
    });

    let passed = 0;
    let warned = 0;
    let failed = 0;

    function check(name, status, detail = '') {
        const icon = status === 'pass' ? '✓' : status === 'warn' ? '⚠' : '✗';
        console.log(`  ${icon} ${name}${detail ? ' — ' + detail : ''}`);
        if (status === 'pass') passed++;
        else if (status === 'warn') warned++;
        else failed++;
    }

    // Helper: navigate and wait for React mount (SSE-safe — no networkidle)
    async function safeNavigate(url, timeout = 30000) {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
        await page.waitForSelector('#root > *', { timeout: 15000 }).catch(() => {});
        await page.waitForTimeout(1500); // Allow React to hydrate
        return resp;
    }

    // ── 1. Page Load ──────────────────────────────────────────
    console.log('\n1. PAGE LOAD');
    try {
        const response = await safeNavigate(BASE_URL);
        check('HTTP status 200', response?.status() === 200 ? 'pass' : 'fail', `Got ${response?.status()}`);
    } catch (e) {
        check('Page loads', 'fail', e.message.slice(0, 120));
    }

    // Page title
    const title = await page.title();
    check('Page title set', title ? 'pass' : 'warn', `"${title}"`);

    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-initial-load.png`, fullPage: false });

    // ── 2. Console Health ─────────────────────────────────────
    console.log('\n2. CONSOLE HEALTH');
    const realErrors = consoleErrors.filter(e =>
        !e.includes('favicon') && !e.includes('manifest') && !e.includes('DevTools') && !e.includes('sw.js')
    );
    check('No console errors on load', realErrors.length === 0 ? 'pass' : realErrors.length <= 3 ? 'warn' : 'fail',
        realErrors.length > 0 ? `${realErrors.length} errors: ${realErrors.slice(0, 3).join('; ').slice(0, 200)}` : '');

    const hydrationErrors = consoleErrors.filter(e =>
        e.includes('Hydration') || e.includes('hydrat') || e.includes('mismatch')
    );
    check('No React hydration errors', hydrationErrors.length === 0 ? 'pass' : 'fail');

    // ── 3. Core Layout ────────────────────────────────────────
    console.log('\n3. CORE LAYOUT');

    const chatInput = await page.$('textarea, [role="textbox"], [data-testid="chat-input"], input[placeholder*="message" i], input[placeholder*="ask" i], textarea[placeholder]');
    check('Chat input area', chatInput ? 'pass' : 'fail');

    const header = await page.$('header, nav, [role="navigation"], [data-testid="navigation"]');
    check('Navigation/header', header ? 'pass' : 'fail');

    // ── 4. Model Selector ─────────────────────────────────────
    console.log('\n4. MODEL SELECTOR');
    const bodyText = await page.textContent('body');
    const hasModelRef = /flash|gemini|pro|opus|model/i.test(bodyText || '');
    check('Model selection available', hasModelRef ? 'pass' : 'warn', hasModelRef ? 'Found model reference' : 'No model text found');

    const modelDropdown = await page.$('select, [role="combobox"], [class*="select" i]');
    check('Dropdown component present', modelDropdown ? 'pass' : 'warn');

    // ── 5. Side Panel Tabs ────────────────────────────────────
    console.log('\n5. SIDE PANEL');

    // The side panel may start collapsed — expand it first
    const expandBtn = await page.$('[data-testid="expandPanel"]');
    if (expandBtn) {
        await expandBtn.click();
        await page.waitForTimeout(800);
        check('Expanded side panel', 'pass');
    } else {
        check('Side panel already expanded', 'pass');
    }

    // Tab triggers use title attributes: "Files", "Activity", "Sources", "Data Sources", "Session Memory", "Agent Performance"
    const filesTab = await page.$('[title="Files"]');
    const activityTab = await page.$('[title="Activity"]');
    const dataSourcesTab = await page.$('[title="Data Sources"]');
    const memoryTab = await page.$('[title="Session Memory"]');
    const perfTab = await page.$('[title="Agent Performance"]');

    check('Files tab', filesTab ? 'pass' : 'warn');
    check('Activity tab', activityTab ? 'pass' : 'warn');
    check('Data Sources tab', dataSourcesTab ? 'pass' : 'warn');
    check('Session Memory tab', memoryTab ? 'pass' : 'warn');
    check('Performance tab', perfTab ? 'pass' : 'warn');

    // Sources tab only shows when session has sources — OK to be absent
    const sourcesTab = await page.$('[title="Sources"]');
    check('Sources tab', sourcesTab ? 'pass' : 'warn', sourcesTab ? '' : 'Hidden (no active sources — expected)');

    await page.screenshot({ path: `${SCREENSHOT_DIR}/02-side-panel.png`, fullPage: false });

    // ── 6. Data Sources Panel ─────────────────────────────────
    console.log('\n6. DATA SOURCES PANEL');

    if (dataSourcesTab) {
        await dataSourcesTab.click();
        await page.waitForTimeout(2000); // Allow health fetch
        const dsBody = await page.textContent('body');
        const hasMcpContent = /pubmed|clinicaltrials|openfda|mcp|data source/i.test(dsBody || '');
        check('MCP source names rendered', hasMcpContent ? 'pass' : 'warn');

        const hasHealthStatus = /online|offline|available|known sources|could not reach/i.test(dsBody || '');
        check('Health status indicators', hasHealthStatus ? 'pass' : 'warn');

        await page.screenshot({ path: `${SCREENSHOT_DIR}/03-datasources.png`, fullPage: false });
    } else {
        check('Data Sources panel', 'warn', 'Tab not found — skipping');
    }

    // ── 7. Knowledge Graph Page (hash route) ──────────────────
    console.log('\n7. KNOWLEDGE GRAPH');

    try {
        // Hash routing: navigate to /#/knowledge-graph
        await safeNavigate(BASE_URL + '/#/knowledge-graph');
        // Check URL has knowledge-graph
        const kgUrl = page.url();
        check('KG page route', kgUrl.includes('knowledge-graph') ? 'pass' : 'fail', kgUrl);

        const kgBody = await page.textContent('body');
        const hasGraphUI = /graph|knowledge|session|query|explore/i.test(kgBody || '');
        check('KG UI elements present', hasGraphUI ? 'pass' : 'warn');

        const graphContainer = await page.$('canvas, [class*="cytoscape" i], [class*="graph" i], [class*="Graph"]');
        check('Graph container', graphContainer ? 'pass' : 'warn', graphContainer ? 'Found' : 'May render on-demand with session data');

        const queryInput = await page.$('input[placeholder*="knowledge" i], input[placeholder*="query" i], input[placeholder*="search" i], input[placeholder*="cypher" i], textarea[placeholder*="query" i]');
        check('Graph query input', queryInput ? 'pass' : 'warn');

        await page.screenshot({ path: `${SCREENSHOT_DIR}/04-knowledge-graph.png`, fullPage: false });
    } catch (e) {
        check('KG page', 'fail', e.message.slice(0, 120));
    }

    // ── 8. Agent Configs Page (hash route) ────────────────────
    console.log('\n8. AGENT CONFIGS');

    try {
        await safeNavigate(BASE_URL + '/#/agents');
        const agentBody = await page.textContent('body');
        const hasAgentNames = /orchestrator|specialist|verifier|triage/i.test(agentBody || '');
        check('Agent Configs page', 'pass');
        check('Agent names listed', hasAgentNames ? 'pass' : 'warn');

        await page.screenshot({ path: `${SCREENSHOT_DIR}/05-agent-configs.png`, fullPage: false });
    } catch (e) {
        check('Agent Configs', 'fail', e.message.slice(0, 120));
    }

    // ── 9. Chat & SSE ─────────────────────────────────────────
    console.log('\n9. CHAT & SSE');

    try {
        await safeNavigate(BASE_URL + '/#/chat');
        // Wait for chat input — it's a contentEditable div with data-testid="chat-input"
        try {
            await page.waitForSelector('[data-testid="chat-input"]', { timeout: 10000 });
        } catch {
            // Fallback: full page reload on root (auto-redirects to /#/chat)
            await safeNavigate(BASE_URL);
            await page.waitForSelector('[data-testid="chat-input"]', { timeout: 10000 }).catch(() => {});
        }

        const input = await page.$('[data-testid="chat-input"]');
        if (input) {
            // contentEditable — use click + type instead of fill
            await input.click();
            await page.keyboard.type('What is aspirin used for? Reply in one sentence.');

            // Send button has data-testid="sendMessage"
            const sendBtn = await page.$('[data-testid="sendMessage"]');
            if (sendBtn && await sendBtn.isEnabled()) {
                await sendBtn.click();
            } else {
                await page.keyboard.press('Enter');
            }
            check('Message sent', 'pass');

            // Wait for streaming response
            try {
                await page.waitForSelector('[class*="message"], [class*="response"], [class*="assistant"], [data-role="assistant"], [class*="streaming"], [class*="markdown"]', { timeout: 25000 });
                check('SSE response element appeared', 'pass');
            } catch {
                check('SSE response element', 'warn', 'No response element detected in 25s');
            }

            // Check for protocol stepper (may appear for research queries)
            const stepper = await page.$('[class*="stepper" i], [class*="protocol" i], [class*="step" i][class*="research" i]');
            check('Protocol stepper', stepper ? 'pass' : 'warn', stepper ? 'Visible' : 'Not shown (expected for simple query)');

            await page.waitForTimeout(3000);
            await page.screenshot({ path: `${SCREENSHOT_DIR}/06-chat-response.png`, fullPage: false });
        } else {
            check('Chat input for SSE test', 'fail', 'No input found');
        }
    } catch (e) {
        check('Chat & SSE', 'fail', e.message.slice(0, 120));
    }

    // ── 10. Triage Mode ───────────────────────────────────────
    console.log('\n10. TRIAGE MODE');

    // Mode options are inside a Select component — need to open it
    // The combobox may be disabled during SSE response, so use force click with timeout
    let foundTriage = false;
    try {
        const modeSelectors = await page.$$('[role="combobox"]');
        for (const sel of modeSelectors) {
            const text = await sel.textContent().catch(() => '');
            if (/research|triage|deep|mode/i.test(text || '')) {
                // Force click even if disabled (we just want to peek at the options)
                await sel.click({ force: true, timeout: 5000 });
                await page.waitForTimeout(500);
                const dropdownText = await page.textContent('body');
                foundTriage = /triage/i.test(dropdownText || '');
                await page.keyboard.press('Escape');
                break;
            }
        }
    } catch {
        // Combobox click failed — check page source for triage reference instead
    }
    // Fallback: check page HTML for triage option even if dropdown didn't open
    if (!foundTriage) {
        const pageHtml = await page.content();
        foundTriage = /triage/i.test(pageHtml || '');
    }
    check('Triage mode available', foundTriage ? 'pass' : 'warn');

    // ── 11. API Endpoints ─────────────────────────────────────
    console.log('\n11. API ENDPOINTS');

    // Health endpoint is at /health (not /api/v1/health)
    try {
        const healthResp = await page.evaluate(async (base) => {
            try {
                const r = await fetch(base + '/health');
                return { status: r.status, ok: r.ok };
            } catch (e) {
                return { error: e.message };
            }
        }, BASE_URL);
        check('/health', healthResp.ok ? 'pass' : 'warn', healthResp.ok ? `HTTP ${healthResp.status}` : healthResp.error || `HTTP ${healthResp.status}`);
    } catch (e) {
        check('/health', 'warn', e.message.slice(0, 100));
    }

    // Also check /api/v1/health alias (if backend has it)
    try {
        const healthV1Resp = await page.evaluate(async (base) => {
            try {
                const r = await fetch(base + '/api/v1/health');
                return { status: r.status, ok: r.ok };
            } catch (e) {
                return { error: e.message };
            }
        }, BASE_URL);
        check('/api/v1/health (alias)', healthV1Resp.ok ? 'pass' : 'warn', healthV1Resp.ok ? `HTTP ${healthV1Resp.status}` : `HTTP ${healthV1Resp.status} (optional alias)`);
    } catch (e) {
        check('/api/v1/health (alias)', 'warn', e.message.slice(0, 100));
    }

    // DataSources health — note camelCase: /dataSources/health
    try {
        const dsResp = await page.evaluate(async (base) => {
            try {
                const r = await fetch(base + '/api/v1/dataSources/health');
                if (r.ok) {
                    const data = await r.json();
                    return { status: r.status, ok: r.ok, count: typeof data === 'object' ? Object.keys(data).length : 0 };
                }
                return { status: r.status, ok: r.ok };
            } catch (e) {
                return { error: e.message };
            }
        }, BASE_URL);
        check('/api/v1/dataSources/health', dsResp.ok ? 'pass' : 'warn',
            dsResp.ok ? `${dsResp.count} servers` : dsResp.error || `HTTP ${dsResp.status}`);
    } catch (e) {
        check('/api/v1/dataSources/health', 'warn', e.message.slice(0, 100));
    }

    // Graph API
    try {
        const graphResp = await page.evaluate(async (base) => {
            try {
                const r = await fetch(base + '/api/v1/graph/stats');
                return { status: r.status, ok: r.ok };
            } catch (e) {
                return { error: e.message };
            }
        }, BASE_URL);
        check('/api/v1/graph/stats', graphResp.ok ? 'pass' : 'warn',
            graphResp.ok ? `HTTP ${graphResp.status}` : graphResp.error || `HTTP ${graphResp.status}`);
    } catch (e) {
        check('/api/v1/graph/stats', 'warn', e.message.slice(0, 100));
    }

    // Evolution dashboard
    try {
        const evoResp = await page.evaluate(async (base) => {
            try {
                const r = await fetch(base + '/api/v1/evolution/overview');
                return { status: r.status, ok: r.ok };
            } catch (e) {
                return { error: e.message };
            }
        }, BASE_URL);
        check('/api/v1/evolution/overview', evoResp.ok ? 'pass' : 'warn',
            evoResp.ok ? `HTTP ${evoResp.status}` : evoResp.error || `HTTP ${evoResp.status}`);
    } catch (e) {
        check('/api/v1/evolution/overview', 'warn', e.message.slice(0, 100));
    }

    // ── 12. Theme & CSS ───────────────────────────────────────
    console.log('\n12. THEME & CSS');

    const bgColor = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue('--background').trim()
    );
    check('CSS --background var', bgColor ? 'pass' : 'warn', bgColor || 'Not set');

    const primaryColor = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue('--primary').trim()
    );
    check('CSS --primary var', primaryColor ? 'pass' : 'warn', primaryColor || 'Not set');

    // ── 13. Network Errors ────────────────────────────────────
    console.log('\n13. NETWORK');
    const criticalNetErrors = networkErrors.filter(e =>
        !e.includes('/api/') && !e.includes('favicon') && !e.includes('manifest') && !e.includes('sw.js') && !e.includes('/sse')
    );
    check('No critical network failures', criticalNetErrors.length === 0 ? 'pass' : 'warn',
        criticalNetErrors.length > 0 ? criticalNetErrors.slice(0, 3).join('; ').slice(0, 200) : '');

    // ── 14. Accessibility Basics ──────────────────────────────
    console.log('\n14. ACCESSIBILITY');

    const htmlLang = await page.evaluate(() => document.documentElement.lang);
    check('html[lang] set', htmlLang ? 'pass' : 'warn', htmlLang || 'Missing');

    const ariaRoles = await page.evaluate(() => document.querySelectorAll('[role]').length);
    check('ARIA roles present', ariaRoles > 0 ? 'pass' : 'warn', `${ariaRoles} elements`);

    // Final screenshot
    await page.screenshot({ path: `${SCREENSHOT_DIR}/07-final-state.png`, fullPage: true });

    // ── Summary ───────────────────────────────────────────────
    console.log('\n' + '═'.repeat(55));
    console.log(`AUDIT RESULTS: ${passed} passed | ${warned} warnings | ${failed} failed`);
    console.log(`Total checks: ${passed + warned + failed}`);
    console.log(`Screenshots: ${SCREENSHOT_DIR}/`);

    if (consoleWarnings.length > 0) {
        console.log(`\nConsole warnings (${consoleWarnings.length}):`);
        consoleWarnings.slice(0, 5).forEach(w => console.log(`  ⚠ ${w.substring(0, 150)}`));
    }

    if (networkErrors.length > 0) {
        console.log(`\nAll network errors (${networkErrors.length}):`);
        networkErrors.slice(0, 10).forEach(e => console.log(`  → ${e.substring(0, 150)}`));
    }

    console.log('═'.repeat(55) + '\n');

    await browser.close();
    process.exit(failed > 0 ? 1 : 0);
}

run().catch(e => {
    console.error('Audit crashed:', e);
    process.exit(2);
});
