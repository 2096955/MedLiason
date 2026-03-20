import { test as base, expect } from "@playwright/test";
import { MedicalAppPage } from "./medical-app.page";
import { KnowledgeGraphPage } from "./knowledge-graph.page";
import { ConsoleCollector } from "./console-collector";

type MedExpertFixtures = {
    app: MedicalAppPage;
    kg: KnowledgeGraphPage;
    console: ConsoleCollector;
};

export const test = base.extend<MedExpertFixtures>({
    app: async ({ page }, use) => {
        const app = new MedicalAppPage(page);
        await app.goto();
        await app.waitForAppReady();
        await use(app);
    },
    kg: async ({ page }, use) => {
        const kg = new KnowledgeGraphPage(page);
        await use(kg);
    },
    console: async ({ page }, use) => {
        const collector = new ConsoleCollector();
        collector.attach(page);
        await use(collector);
    },
});

export { expect };
