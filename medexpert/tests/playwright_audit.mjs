/**
 * Phase 2 Frontend Audit — Playwright Script
 *
 * Checks:
 * 1. Page loads without console errors
 * 2. Side panel tabs exist and are clickable
 * 3. DataSources panel renders (health API or fallback)
 * 4. Chat input area renders
 * 5. No React hydration errors
 * 6. CSS/layout not broken (viewport screenshot)
 * 7. Sources panel empty state renders
 * 8. Theme loads correctly
 */

import { chromium } from 'playwright';

const BASE_URL = 'http://localhost:3000';
const SCREENSHOT_DIR = '/tmp/playwright-audit';

async function run() {
    console.log('=== MedExpert Frontend Audit ===\n');

    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();

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
    let failed = 0;

    function check(name, condition, detail = '') {
        if (condition) {
            console.log(`  ✓ ${name}`);
            passed++;
        } else {
            console.log(`  ✗ ${name}${detail ? ' — ' + detail : ''}`);
            failed++;
        }
    }

    // ── 1. Page Load ──────────────────────────────────────────
    console.log('\n1. PAGE LOAD');
    try {
        const response = await page.goto(BASE_URL, { waitUntil: 'networkidle', timeout: 30000 });
        check('HTTP status 200', response?.status() === 200, `Got ${response?.status()}`);
    } catch (e) {
        check('Page loads', false, e.message);
    }

    // Take initial screenshot
    await page.screenshot({ path: `${SCREENSHOT_DIR}/01-initial-load.png`, fullPage: false });

    // ── 2. Console Errors ─────────────────────────────────────
    console.log('\n2. CONSOLE HEALTH');
    // Filter out known non-critical warnings
    const realErrors = consoleErrors.filter(e =>
        !e.includes('favicon') &&
        !e.includes('manifest') &&
        !e.includes('DevTools')
    );
    check('No console errors on load', realErrors.length === 0,
        realErrors.length > 0 ? `${realErrors.length} errors: ${realErrors.slice(0, 3).join('; ')}` : '');

    const hydrationErrors = consoleErrors.filter(e =>
        e.includes('Hydration') || e.includes('hydrat') || e.includes('mismatch')
    );
    check('No React hydration errors', hydrationErrors.length === 0);

    // ── 3. Core Layout ────────────────────────────────────────
    console.log('\n3. CORE LAYOUT');

    // Check for chat input area
    const chatInput = await page.$('textarea, [role="textbox"], [data-testid="chat-input"], input[placeholder*="message" i], input[placeholder*="ask" i], textarea[placeholder]');
    check('Chat input area exists', chatInput !== null);

    // Check for navigation/header
    const header = await page.$('header, nav, [role="navigation"], [data-testid="navigation"]');
    check('Navigation header exists', header !== null);

    // ── 4. Side Panel ─────────────────────────────────────────
    console.log('\n4. SIDE PANEL TABS');

    // Look for tab triggers or side panel
    const allButtons = await page.$$('button');
    const buttonTexts = [];
    for (const btn of allButtons) {
        const text = await btn.textContent().catch(() => '');
        buttonTexts.push(text?.trim() || '');
    }

    // Check for Files/Activity/Sources/Data Sources tabs or buttons
    const hasFilesTab = buttonTexts.some(t => /files/i.test(t));
    const hasActivityTab = buttonTexts.some(t => /activity/i.test(t));
    const hasSourcesTab = buttonTexts.some(t => /sources/i.test(t));
    const hasDataSourcesTab = buttonTexts.some(t => /data\s*sources/i.test(t));

    check('Files tab visible', hasFilesTab, hasFilesTab ? '' : 'May be collapsed — checking for side panel toggle');
    check('Activity tab visible', hasActivityTab);

    // Try to find and click a side panel toggle if tabs aren't visible
    if (!hasFilesTab && !hasActivityTab) {
        const toggleBtn = await page.$('[data-testid="side-panel-toggle"], button[aria-label*="panel" i], button[aria-label*="sidebar" i]');
        if (toggleBtn) {
            await toggleBtn.click();
            await page.waitForTimeout(500);
            console.log('  (Clicked side panel toggle)');

            const afterButtons = await page.$$('button');
            for (const btn of afterButtons) {
                const text = await btn.textContent().catch(() => '');
                const t = text?.trim() || '';
                if (/files/i.test(t)) check('Files tab (after toggle)', true);
                if (/activity/i.test(t)) check('Activity tab (after toggle)', true);
            }
        }
    }

    await page.screenshot({ path: `${SCREENSHOT_DIR}/02-side-panel.png`, fullPage: false });

    // ── 5. DataSources Panel ──────────────────────────────────
    console.log('\n5. DATA SOURCES PANEL');

    // Try clicking Data Sources tab
    for (const btn of allButtons) {
        const text = await btn.textContent().catch(() => '');
        if (/data\s*sources/i.test(text?.trim() || '')) {
            await btn.click();
            await page.waitForTimeout(1000);
            break;
        }
    }

    // Check for MCP source indicators or fallback list
    const pageText = await page.textContent('body');
    const hasMcpContent = /pubmed|clinicaltrials|openfda|mcp|data source/i.test(pageText || '');
    check('DataSources content present', hasMcpContent, hasMcpContent ? '' : 'No MCP source names found in page');

    // Check for health status indicators
    const hasFallback = /known sources|could not reach|showing known/i.test(pageText || '');
    const hasOnlineStatus = /online|offline|available/i.test(pageText || '');
    check('Health status or fallback shown', hasFallback || hasOnlineStatus);

    await page.screenshot({ path: `${SCREENSHOT_DIR}/03-datasources.png`, fullPage: false });

    // ── 6. Empty States ───────────────────────────────────────
    console.log('\n6. EMPTY STATES');

    const hasEmptyState = /no sources|no files|no activity|no messages|start.*conversation/i.test(pageText || '');
    check('Empty state message present', hasEmptyState);

    // ── 7. Network Errors ─────────────────────────────────────
    console.log('\n7. NETWORK');

    // Filter out expected failures (backend not running)
    const criticalNetErrors = networkErrors.filter(e =>
        !e.includes('/api/') &&  // Backend APIs expected to fail
        !e.includes('favicon') &&
        !e.includes('manifest')
    );
    check('No critical network failures', criticalNetErrors.length === 0,
        criticalNetErrors.length > 0 ? criticalNetErrors.slice(0, 3).join('; ') : '');

    // Log API failures (expected when backend isn't running)
    const apiFailures = networkErrors.filter(e => e.includes('/api/'));
    if (apiFailures.length > 0) {
        console.log(`  ℹ ${apiFailures.length} API calls failed (expected — backend not running)`);
        apiFailures.slice(0, 5).forEach(e => console.log(`    ${e}`));
    }

    // ── 8. Theme ──────────────────────────────────────────────
    console.log('\n8. THEME');

    // Check CSS custom properties loaded
    const bgColor = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue('--background').trim()
    );
    check('CSS custom properties loaded', bgColor !== '', bgColor ? `--background: ${bgColor}` : 'No --background CSS var');

    // Final screenshot
    await page.screenshot({ path: `${SCREENSHOT_DIR}/04-final-state.png`, fullPage: false });

    // ── Summary ───────────────────────────────────────────────
    console.log('\n═══════════════════════════════════════');
    console.log(`RESULTS: ${passed} passed, ${failed} failed`);
    console.log(`Screenshots saved to: ${SCREENSHOT_DIR}/`);

    if (consoleWarnings.length > 0) {
        console.log(`\nConsole warnings (${consoleWarnings.length}):`);
        consoleWarnings.slice(0, 5).forEach(w => console.log(`  ⚠ ${w.substring(0, 120)}`));
    }

    console.log('═══════════════════════════════════════\n');

    await browser.close();
    process.exit(failed > 0 ? 1 : 0);
}

run().catch(e => {
    console.error('Playwright audit failed:', e);
    process.exit(2);
});
