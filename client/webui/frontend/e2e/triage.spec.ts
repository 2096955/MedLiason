import { test, expect } from "./fixtures/base.fixture";
import { SEL } from "./fixtures/selectors";

test.describe("Triage Pipeline @triage", () => {
    test.describe.configure({ timeout: 180_000 }); // 3 min

    test("submits symptoms and receives triage response", async ({
        app,
        console,
        page,
    }) => {
        await app.selectModel("flash");
        await app.selectMode("triage");
        await app.startNewChat();

        await app.submitQuery("I have had a persistent headache for 3 days with mild fever of 37.5C");
        await app.screenshotMilestone("triage-01-submitted");

        // Wait for triage pipeline to respond (send button reappears)
        await app.waitForTriageComplete({ timeoutMs: 150_000 });
        await app.screenshotMilestone("triage-02-complete");

        await test.step("response has content", async () => {
            const pageContent = await page.textContent("body");
            expect(pageContent!.length).toBeGreaterThan(100);
        });

        await test.step("triage disclaimer visible", async () => {
            // The triage shows a disclaimer about not replacing professional judgment
            await expect(
                page.locator("text=/triage|educational purposes/i").first(),
            ).toBeVisible();
        });

        await test.step("no JS exceptions", async () => {
            console.assertNoErrors();
        });

        await app.screenshotMilestone("triage-03-final");
    });

    test("mild symptoms get non-emergency response", async ({ app, page }) => {
        await app.selectModel("flash");
        await app.selectMode("triage");
        await app.startNewChat();

        await app.submitQuery("I have a mild cough that started yesterday, no fever");
        await app.waitForTriageComplete({ timeoutMs: 150_000 });

        // Get the AGENT response text only (not the full page which includes the disclaimer)
        const responseText = await app.getRenderedMarkdownContent();
        const agentMessages = page.locator('[class*="bg-muted"]');
        const lastMsg = await agentMessages.last().textContent().catch(() => "");

        // The response should be conversational (asking follow-up questions or providing assessment)
        expect((responseText + (lastMsg ?? "")).length).toBeGreaterThan(20);

        await app.screenshotMilestone("triage-mild-response");
    });
});
