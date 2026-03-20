import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
    testDir: "./e2e",
    timeout: 240_000, // 4 min — covers worst-case Flash research (176s) + margin
    expect: { timeout: 10_000 },
    fullyParallel: false,
    workers: 1, // serial — tests share a live backend
    retries: 0, // retries are wasteful at 1-3 min per pipeline test
    reporter: [
        ["html", { open: "never" }],
        ["json", { outputFile: "e2e/test-results/results.json" }],
    ],
    outputDir: "e2e/test-results",
    use: {
        baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
        screenshot: "on",
        trace: "retain-on-failure",
        video: "retain-on-failure",
        viewport: { width: 1280, height: 720 },
        actionTimeout: 15_000,
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
});
