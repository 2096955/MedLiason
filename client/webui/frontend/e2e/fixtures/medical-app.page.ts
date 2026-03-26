import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { SEL } from "./selectors";

/**
 * Page object for the MedExpert chat interface.
 * Encapsulates query submission, pipeline completion detection,
 * and content extraction for E2E assertions.
 */
export class MedicalAppPage {
    constructor(private page: Page) {}

    // ── Navigation ──

    async goto() {
        await this.page.goto("/", { waitUntil: "domcontentloaded" });
    }

    /** Wait for the app to be ready (model selector visible = config fetched). */
    async waitForAppReady() {
        await this.page.locator(SEL.modelSelect).waitFor({ state: "visible", timeout: 30_000 });
    }

    // ── Model / Mode ──

    /**
     * Select a model from the dropdown.
     * SelectItem children show shortLabel: "Flash", "Pro", "Opus".
     */
    async selectModel(model: "flash" | "pro" | "opus") {
        const shortLabels: Record<string, string> = {
            flash: "Flash",
            pro: "Pro",
            opus: "Opus",
        };
        await this.page.locator(SEL.modelSelect).click();
        await this.page.locator(`${SEL.selectItem}:has-text("${shortLabels[model]}")`).click();
        await this.page.waitForTimeout(300);
    }

    /**
     * Select a mode from the dropdown.
     * SelectItem children show shortLabel: "Research", "Triage".
     */
    async selectMode(mode: "research" | "triage") {
        const shortLabels: Record<string, string> = {
            research: "Research",
            triage: "Triage",
        };
        await this.page.locator(SEL.modeSelect).click();
        await this.page.locator(`${SEL.selectItem}:has-text("${shortLabels[mode]}")`).click();
        await this.page.waitForTimeout(300);
    }

    // ── Session management ──

    async startNewChat() {
        const newChatBtn = this.page.locator(SEL.startNewChat);
        if (await newChatBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
            await newChatBtn.click();
            // Handle confirmation dialog if present
            const confirmBtn = this.page.locator('[data-testid="dialogConfirmButton"]');
            if (await confirmBtn.isVisible({ timeout: 1_000 }).catch(() => false)) {
                await confirmBtn.click();
            }
            await this.page.waitForTimeout(500);
        }
    }

    // ── Submit query ──

    async submitQuery(text: string) {
        const input = this.page.locator(SEL.chatInput);
        await input.click();
        await input.fill(text);
        await this.page.locator(SEL.sendButton).click();
    }

    // ── Pipeline completion detection ──

    /**
     * Wait for the research pipeline to complete.
     * Primary signal: "Research complete" text in ResearchProtocolStepper.
     * Fallback: send button reappears (isResponding=false).
     */
    async waitForResearchComplete(options?: { timeoutMs?: number }) {
        const timeout = options?.timeoutMs ?? 200_000;

        await this.page
            .locator("text=Research complete")
            .or(this.page.locator(SEL.sendButton))
            .first()
            .waitFor({ state: "visible", timeout });

        // Extra guard: send button must be visible (pipeline fully done)
        await this.page.locator(SEL.sendButton).waitFor({ state: "visible", timeout: 30_000 });
    }

    /**
     * Wait for the triage pipeline to complete.
     * Primary signal: NBA "Recommendation" section appears.
     * Fallback: send button reappears.
     */
    async waitForTriageComplete(options?: { timeoutMs?: number }) {
        const timeout = options?.timeoutMs ?? 150_000;

        await this.page
            .locator("text=Recommendation")
            .or(this.page.locator(SEL.sendButton))
            .first()
            .waitFor({ state: "visible", timeout });

        await this.page.locator(SEL.sendButton).waitFor({ state: "visible", timeout: 30_000 });
    }

    // ── Content extraction ──

    /** Get the text content of the last rendered markdown response. */
    async getRenderedMarkdownContent(): Promise<string> {
        const proseBlocks = this.page.locator(".prose");
        const count = await proseBlocks.count();
        if (count === 0) {
            // Fallback: get last agent message text content
            const messages = this.page.locator('[class*="bg-muted"]');
            const msgCount = await messages.count();
            return msgCount > 0 ? ((await messages.last().textContent()) ?? "") : "";
        }
        return (await proseBlocks.last().textContent()) ?? "";
    }

    /** Check if the rendered response contains markdown formatting (headers, lists, bold). */
    async hasMarkdownFormatting(): Promise<boolean> {
        const prose = this.page.locator(".prose").last();
        if (!(await prose.isVisible().catch(() => false))) return false;
        const hasHeadings = (await prose.locator("h1, h2, h3, h4").count()) > 0;
        const hasLists = (await prose.locator("ul, ol").count()) > 0;
        const hasBold = (await prose.locator("strong").count()) > 0;
        return hasHeadings || hasLists || hasBold;
    }

    /** Count completed (green) protocol steps in the stepper. */
    async getCompletedStepCount(): Promise<number> {
        // Green CheckCircle icons rendered with text-green-500
        return await this.page.locator(".text-green-500 svg, svg.text-green-500").count();
    }

    /** Check if protocol stepper is visible. */
    async isProtocolStepperVisible(): Promise<boolean> {
        return await this.page
            .locator(SEL.protocolToggle)
            .isVisible()
            .catch(() => false);
    }

    // ── Screenshots ──

    async screenshotMilestone(name: string) {
        await this.page.screenshot({
            path: `e2e/screenshots/${name}-${Date.now()}.png`,
            fullPage: false,
        });
    }
}
