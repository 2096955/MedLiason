import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Collects JavaScript console errors and uncaught exceptions during a test.
 * Filters known noise (SSE reconnects, ResizeObserver, etc.) and asserts
 * zero unhandled exceptions at test teardown.
 */
export class ConsoleCollector {
    errors: string[] = [];
    uncaughtExceptions: string[] = [];

    private static IGNORE_PATTERNS = [
        /Failed to fetch/, // SSE reconnect attempts
        /net::ERR_/, // Network errors during SSE
        /ResizeObserver/, // Benign ResizeObserver warnings
        /Error parsing SSE/, // Transient SSE parse errors
        /EventSource/, // EventSource connection noise
        /AbortError/, // Cancelled fetch requests
        /hydrat/i, // React hydration warnings (dev mode)
    ];

    attach(page: Page) {
        page.on("console", (msg) => {
            if (msg.type() === "error") {
                const text = msg.text();
                if (!ConsoleCollector.IGNORE_PATTERNS.some((p) => p.test(text))) {
                    this.errors.push(text);
                }
            }
        });
        page.on("pageerror", (error) => {
            const msg = error.message;
            if (!ConsoleCollector.IGNORE_PATTERNS.some((p) => p.test(msg))) {
                this.uncaughtExceptions.push(msg);
            }
        });
    }

    /** Assert no uncaught exceptions occurred. Console.errors are collected but not fatal. */
    assertNoErrors() {
        expect(
            this.uncaughtExceptions,
            `Uncaught JS exceptions during test:\n${this.uncaughtExceptions.join("\n")}`,
        ).toEqual([]);
    }

    reset() {
        this.errors = [];
        this.uncaughtExceptions = [];
    }
}
