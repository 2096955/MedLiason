import type { Page } from "@playwright/test";
import { SEL } from "./selectors";

/**
 * Page object for the Knowledge Graph visualization page.
 */
export class KnowledgeGraphPage {
    constructor(private page: Page) {}

    async navigate() {
        await this.page.goto("/#/knowledge-graph", { waitUntil: "domcontentloaded" });
        await this.page.locator("text=Knowledge Graph").first().waitFor({ state: "visible", timeout: 15_000 });
    }

    async waitForGraphLoad() {
        // Wait for either graph nodes or empty state
        await this.page
            .locator(SEL.kgNode)
            .or(this.page.locator("text=No graph data"))
            .first()
            .waitFor({ state: "visible", timeout: 15_000 });
    }

    async getNodeCount(): Promise<number> {
        return await this.page.locator(SEL.kgNode).count();
    }

    async hasColumnHeaders(): Promise<boolean> {
        const columns = ["SESSIONS", "SPECIALISTS", "SHARED ENTITIES", "STUDIES"];
        for (const col of columns) {
            if ((await this.page.locator(`text=${col}`).count()) === 0) return false;
        }
        return true;
    }

    async clickEntityFilter(type: "Disease" | "Drug" | "Gene" | "All") {
        await this.page.locator(`button:has-text("${type}")`).click();
        await this.page.waitForTimeout(500);
    }

    async hasStats(): Promise<boolean> {
        const nodesVisible = await this.page.locator("text=/\\d+ nodes/").isVisible().catch(() => false);
        const edgesVisible = await this.page.locator("text=/\\d+ edges/").isVisible().catch(() => false);
        return nodesVisible && edgesVisible;
    }

    async hasNlqBar(): Promise<boolean> {
        return await this.page
            .locator('input[placeholder*="knowledge graph"], input[placeholder*="Ask"], input[placeholder*="query"]')
            .isVisible()
            .catch(() => false);
    }

    async screenshotMilestone(name: string) {
        await this.page.screenshot({
            path: `e2e/screenshots/${name}-${Date.now()}.png`,
            fullPage: false,
        });
    }
}
