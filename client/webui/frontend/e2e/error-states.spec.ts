import { test, expect } from "./fixtures/base.fixture";
import { SEL } from "./fixtures/selectors";

test.describe("Error Resilience @error", () => {
    test.describe.configure({ timeout: 240_000 }); // 4 min

    test("broad query produces response despite potential partial failures", async ({
        app,
        console,
        page,
    }) => {
        await app.selectModel("flash");
        await app.selectMode("research");
        await app.startNewChat();

        // Query that exercises multiple specialists — some MCP servers may fail
        await app.submitQuery("What are the side effects of statins?");
        await app.waitForResearchComplete({ timeoutMs: 200_000 });

        await test.step("pipeline completes", async () => {
            await expect(
                page.locator("text=Research complete").or(page.locator(SEL.sendButton)),
            ).toBeVisible();
        });

        await test.step("response is not empty", async () => {
            const responseText = await app.getRenderedMarkdownContent();
            // Even with partial failures, some content should render (e.g. "Research Inconclusive")
            expect(responseText.length).toBeGreaterThan(10);
        });

        await test.step("no uncaught JS exceptions", async () => {
            console.assertNoErrors();
        });

        await app.screenshotMilestone("error-resilience-final");
    });

    test("no JS exceptions during triage pipeline", async ({ app, console }) => {
        await app.selectModel("flash");
        await app.selectMode("triage");
        await app.startNewChat();

        await app.submitQuery("I have a mild sore throat for 2 days");
        await app.waitForTriageComplete({ timeoutMs: 150_000 });

        console.assertNoErrors();
    });
});
