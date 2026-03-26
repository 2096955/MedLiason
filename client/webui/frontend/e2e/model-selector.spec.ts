import { test, expect } from "./fixtures/base.fixture";
import { SEL } from "./fixtures/selectors";

test.describe("Model & Mode Selector @selector", () => {
    test.describe.configure({ timeout: 60_000 });

    // NOTE: All tests must destructure `app` to trigger the fixture's goto() + waitForAppReady()

    test("renders model and mode dropdowns with labels", async ({ app, page }) => {
        await expect(page.locator(SEL.modelSelect)).toBeVisible();
        await expect(page.locator(SEL.modeSelect)).toBeVisible();
        // Labels visible on desktop (md: breakpoint, viewport 1280px)
        await expect(page.getByText("Model", { exact: true })).toBeVisible();
        await expect(page.getByText("Mode", { exact: true })).toBeVisible();
    });

    test("defaults to Flash + Research after localStorage clear", async ({ app, page }) => {
        await page.evaluate(() => {
            localStorage.removeItem("medexpert-model");
            localStorage.removeItem("medexpert-mode");
        });
        await page.reload({ waitUntil: "domcontentloaded" });
        await app.waitForAppReady();

        await expect(page.locator(SEL.modelSelect)).toContainText(/Flash/);
        await expect(page.locator(SEL.modeSelect)).toContainText(/Research/);
    });

    test("shows 3 model options", async ({ app, page }) => {
        await page.locator(SEL.modelSelect).click();
        await expect(page.locator(`${SEL.selectItem}:has-text("Flash")`)).toBeVisible();
        await expect(page.locator(`${SEL.selectItem}:has-text("Pro")`)).toBeVisible();
        await expect(page.locator(`${SEL.selectItem}:has-text("Opus")`)).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("shows 2 mode options", async ({ app, page }) => {
        await page.locator(SEL.modeSelect).click();
        await expect(page.locator(`${SEL.selectItem}:has-text("Research")`)).toBeVisible();
        await expect(page.locator(`${SEL.selectItem}:has-text("Triage")`)).toBeVisible();
        await page.keyboard.press("Escape");
    });

    test("persists model selection across reload", async ({ app, page }) => {
        await page.locator(SEL.modelSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Pro")`).click();
        await page.waitForTimeout(300);
        await expect(page.locator(SEL.modelSelect)).toContainText(/Pro/);

        await page.reload({ waitUntil: "domcontentloaded" });
        await app.waitForAppReady();
        await expect(page.locator(SEL.modelSelect)).toContainText(/Pro/);

        // Reset to Flash
        await page.locator(SEL.modelSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Flash")`).click();
    });

    test("persists mode selection across reload", async ({ app, page }) => {
        await page.locator(SEL.modeSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Triage")`).click();
        await page.waitForTimeout(300);

        await page.reload({ waitUntil: "domcontentloaded" });
        await app.waitForAppReady();
        await expect(page.locator(SEL.modeSelect)).toContainText(/Triage/);

        // Reset to Research
        await page.locator(SEL.modeSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Research")`).click();
    });

    test("model change forces new session (clears chat)", async ({ app, page }) => {
        await app.selectModel("flash");
        await app.selectMode("research");

        await app.submitQuery("Hello");
        await page.locator(SEL.sendButton).waitFor({ state: "visible", timeout: 60_000 });

        const messagesBefore = await page.locator('[class*="bg-muted"]').count();
        expect(messagesBefore).toBeGreaterThan(0);

        await page.locator(SEL.modelSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Pro")`).click();
        await page.waitForTimeout(1_000);

        const messagesAfter = await page.locator('[class*="bg-muted"]').count();
        expect(messagesAfter).toBe(0);

        // Reset to Flash
        await page.locator(SEL.modelSelect).click();
        await page.locator(`${SEL.selectItem}:has-text("Flash")`).click();
    });

    test("send button is disabled when input is empty", async ({ app, page }) => {
        const sendBtn = page.locator(SEL.sendButton);
        await expect(sendBtn).toBeVisible();
        await expect(sendBtn).toBeDisabled();
    });
});
