import { test, expect } from "./fixtures/base.fixture";
import { SEL } from "./fixtures/selectors";

/**
 * Knowledge Graph page tests — NO research pipeline needed.
 * Tests the KG page rendering, filters, legend, and NLQ bar.
 * Graph data may or may not be present (depends on prior sessions).
 */
test.describe("Knowledge Graph @kg", () => {
    test.describe.configure({ timeout: 60_000 }); // 1 min — no pipeline

    test("page renders with title, filter buttons, and stats", async ({
        kg,
        console,
        page,
    }) => {
        await kg.navigate();
        await kg.waitForGraphLoad();
        await kg.screenshotMilestone("kg-01-loaded");

        await test.step("page title visible", async () => {
            await expect(page.locator("text=Knowledge Graph").first()).toBeVisible();
        });

        await test.step("filter buttons rendered", async () => {
            // Filter buttons: Disease, Drug, Gene, Study, All
            for (const label of ["Disease", "Drug", "Gene", "All"]) {
                await expect(page.locator(`button:has-text("${label}")`)).toBeVisible();
            }
        });

        await test.step("stats display (nodes + edges count)", async () => {
            // Header shows "N nodes | N edges" — even "0 nodes" is valid
            await expect(page.locator("text=/\\d+ nodes/")).toBeVisible();
            await expect(page.locator("text=/\\d+ edges/")).toBeVisible();
        });

        await test.step("SVG or empty state present", async () => {
            const svgCount = await page.locator("svg").count();
            const emptyState = await page.locator("text=No graph data").isVisible().catch(() => false);
            expect(svgCount > 0 || emptyState).toBe(true);
        });

        await test.step("no JS exceptions", async () => {
            console.assertNoErrors();
        });

        await kg.screenshotMilestone("kg-02-final");
    });

    test("entity filter buttons are interactive", async ({ kg, page }) => {
        await kg.navigate();
        await kg.waitForGraphLoad();

        for (const filter of ["Disease", "Drug", "Gene", "All"] as const) {
            await kg.clickEntityFilter(filter);
        }

        // Page should still be functional after filtering
        await expect(page.locator("text=Knowledge Graph").first()).toBeVisible();
    });

    test("edge legend shows expected labels when graph has data", async ({ kg, page }) => {
        await kg.navigate();
        await kg.waitForGraphLoad();

        const nodeCount = await kg.getNodeCount();
        if (nodeCount === 0) {
            // No graph data — legend may not render, skip assertion
            test.skip();
            return;
        }

        const edgeTypes = ["QUERIED", "FOUND", "EVIDENCED_BY", "CITED"];
        for (const edgeType of edgeTypes) {
            await expect(page.locator(`text=${edgeType}`)).toBeVisible();
        }
    });

    test("NLQ query bar is present", async ({ kg }) => {
        await kg.navigate();
        expect(await kg.hasNlqBar()).toBe(true);
    });
});
