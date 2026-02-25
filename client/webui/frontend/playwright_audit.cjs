const { chromium } = require("playwright");

(async () => {
    console.log("=== MedExpert Frontend Audit ===");
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();

    const errors = [];
    const warnings = [];
    const netErrors = [];
    page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
        if (m.type() === "warning") warnings.push(m.text());
    });
    page.on("requestfailed", (r) => {
        netErrors.push(r.method() + " " + r.url() + " — " + (r.failure() || {}).errorText);
    });

    let pass = 0;
    let fail = 0;
    function chk(name, ok, detail) {
        if (ok) {
            console.log("  OK: " + name);
            pass++;
        } else {
            console.log("  FAIL: " + name + (detail ? " — " + detail : ""));
            fail++;
        }
    }

    // 1. Load
    console.log("\n1. PAGE LOAD");
    const resp = await page.goto("http://localhost:3000", {
        waitUntil: "networkidle",
        timeout: 30000,
    });
    chk("HTTP 200", resp.status() === 200, "Got " + resp.status());
    await page.screenshot({ path: "/tmp/playwright-audit/01-load.png" });

    // 2. Console
    console.log("\n2. CONSOLE");
    const real = errors.filter(
        (e) => !e.includes("favicon") && !e.includes("manifest") && !e.includes("DevTools")
    );
    chk(
        "No console errors",
        real.length === 0,
        real.length + " errors: " + real.slice(0, 3).join("; ")
    );
    chk(
        "No hydration errors",
        !errors.some((e) => e.toLowerCase().includes("hydrat"))
    );

    // 3. Layout
    console.log("\n3. LAYOUT");
    const chatInput = await page.$("textarea, [role='textbox'], input[placeholder]");
    chk("Chat input", !!chatInput);
    const header = await page.$("header, nav, [role='navigation']");
    chk("Header/nav", !!header);

    // 4. Body text scan
    console.log("\n4. CONTENT SCAN");
    const body = await page.textContent("body");
    chk(
        "Has UI text",
        /medexpert|orchestrator|research|ask|message|chat/i.test(body || "")
    );
    chk(
        "Has empty state or welcome",
        /no.*source|no.*file|start|new.*conversation|welcome|hello/i.test(body || "")
    );

    // 5. Side panel exploration
    console.log("\n5. SIDE PANEL");
    const btns = await page.$$("button");
    const texts = [];
    for (const b of btns) {
        const t = await b.textContent().catch(() => "");
        texts.push((t || "").trim());
    }
    chk("Buttons rendered", texts.length > 0, texts.length + " buttons");

    const tabs = await page.$$("[role='tab'], [data-state], button[value]");
    chk("Tab elements found", tabs.length > 0, tabs.length + " tab elements");

    // Try panel toggle
    for (const b of btns) {
        const ariaLabel = await b.getAttribute("aria-label").catch(() => "");
        const title = await b.getAttribute("title").catch(() => "");
        if (/panel|sidebar|collapse|expand/i.test((ariaLabel || "") + (title || ""))) {
            console.log("  Found panel toggle: " + (ariaLabel || title));
            await b.click();
            await page.waitForTimeout(500);
            break;
        }
    }
    await page.screenshot({ path: "/tmp/playwright-audit/02-panel.png" });

    // 6. Theme
    console.log("\n6. THEME");
    const bg = await page.evaluate(() =>
        getComputedStyle(document.documentElement).getPropertyValue("--background").trim()
    );
    chk("CSS vars loaded", bg !== "", "--background=" + bg);

    // 7. Network
    console.log("\n7. NETWORK");
    const apiErrs = netErrors.filter((e) => e.includes("/api/"));
    const otherErrs = netErrors.filter(
        (e) => !e.includes("/api/") && !e.includes("favicon")
    );
    chk(
        "No critical net errors",
        otherErrs.length === 0,
        otherErrs.slice(0, 3).join("; ")
    );
    if (apiErrs.length > 0) {
        console.log("  INFO: " + apiErrs.length + " API failures (backend not running)");
        apiErrs.slice(0, 5).forEach((e) => console.log("    " + e));
    }

    // 8. Accessibility
    console.log("\n8. ACCESSIBILITY");
    const lang = await page.evaluate(() => document.documentElement.lang);
    chk("HTML lang attribute", !!lang, "lang=" + lang);
    const titleText = await page.title();
    chk("Page title set", titleText.length > 0, titleText);

    // 9. Screenshot final state
    await page.screenshot({ path: "/tmp/playwright-audit/03-final.png" });

    console.log("\n=== RESULTS: " + pass + " passed, " + fail + " failed ===");
    if (warnings.length > 0) {
        console.log("\nWarnings (" + warnings.length + "):");
        warnings.slice(0, 5).forEach((w) => console.log("  " + w.substring(0, 150)));
    }
    console.log("Screenshots: /tmp/playwright-audit/");

    await browser.close();
    process.exit(fail > 0 ? 1 : 0);
})().catch((e) => {
    console.error("FATAL:", e.message);
    process.exit(2);
});
