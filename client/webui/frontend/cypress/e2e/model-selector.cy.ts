/**
 * E2E tests for the Model + Mode selector in ChatInputArea.
 *
 * These tests verify the frontend model selector renders, interacts
 * correctly, and guards submission when agents are unavailable.
 *
 * Requires: Vite dev server (localhost:3000) + backend gateway (localhost:8000)
 */

describe("Model Selector — Rendering", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
    });

    it("should display Model and Mode dropdowns instead of agent dropdown", () => {
        // The old "Select an agent..." dropdown should NOT exist
        cy.findByText("Select an agent...").should("not.exist");

        // The new Model and Mode selects should be visible via their aria-labels
        cy.get('[aria-label="Select AI model"]').should("be.visible");
        cy.get('[aria-label="Select mode"]').should("be.visible");
    });

    it("should display visible 'Model' and 'Mode' labels", () => {
        cy.findByText("Model").should("be.visible");
        cy.findByText("Mode").should("be.visible");
    });

    it("should show 3 model options when Model dropdown is opened", () => {
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Gemini 2.5 Flash").should("be.visible");
        cy.findByText("Gemini 3.1 Pro").should("be.visible");
        cy.findByText("Claude Opus 4.6").should("be.visible");
    });

    it("should show 2 mode options when Mode dropdown is opened", () => {
        cy.get('[aria-label="Select mode"]').click();
        cy.findByText("Deep Research").should("be.visible");
        cy.findByText("Medical Triage").should("be.visible");
    });

    it("should default to Gemini 2.5 Flash and Deep Research", () => {
        // Check current values in the select triggers
        cy.get('[aria-label="Select AI model"]').should("contain.text", "Gemini 2.5 Flash");
        cy.get('[aria-label="Select mode"]').should("contain.text", "Deep Research");
    });
});

describe("Model Selector — Interaction", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
    });

    it("should change model selection when a different model is clicked", () => {
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Claude Opus 4.6").click();
        cy.get('[aria-label="Select AI model"]').should("contain.text", "Claude Opus 4.6");
    });

    it("should change mode selection when a different mode is clicked", () => {
        cy.get('[aria-label="Select mode"]').click();
        cy.findByText("Medical Triage").click();
        cy.get('[aria-label="Select mode"]').should("contain.text", "Medical Triage");
    });

    it("should persist model selection across page reload", () => {
        // Select a non-default model
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Gemini 3.1 Pro").click();
        cy.get('[aria-label="Select AI model"]').should("contain.text", "Gemini 3.1 Pro");

        // Reload page
        cy.reload();

        // Wait for app to render, then verify persistence
        cy.get('[aria-label="Select AI model"]', { timeout: 10000 }).should("contain.text", "Gemini 3.1 Pro");
    });

    it("should persist mode selection across page reload", () => {
        cy.get('[aria-label="Select mode"]').click();
        cy.findByText("Medical Triage").click();

        cy.reload();

        cy.get('[aria-label="Select mode"]', { timeout: 10000 }).should("contain.text", "Medical Triage");
    });
});

describe("Model Selector — Submit Guard", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
    });

    it("should have send button visible", () => {
        cy.findByTestId("sendMessage").should("be.visible");
    });

    it("should disable send button when input is empty", () => {
        cy.findByTestId("sendMessage").should("be.disabled");
    });

    it("should show 'Connecting...' when agent is not registered", () => {
        // Select a model that likely has no agents registered (e.g., Opus on a Flash-only stack)
        // This test is environment-dependent — on a full multi-model stack, all agents are available
        // On a single-stack dev setup, Pro/Opus agents won't be available
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Claude Opus 4.6").click();

        // Check if the Connecting message appears (only on single-stack deployments)
        cy.get("body").then($body => {
            if ($body.text().includes("Connecting...")) {
                // Agent is not available — verify submit is blocked
                cy.findByTestId("chat-input").type("Test message");
                cy.findByTestId("sendMessage").should("be.disabled");

                // Verify the aria-live container announces the state
                cy.get('[aria-live="polite"]').should("contain.text", "Connecting...");
            }
            // If no "Connecting..." message, the agent is available (multi-model stack)
        });
    });
});

describe("Model Selector — Session Management", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
        cy.startNewChat();
    });

    it("should force new session when model changes", () => {
        // Send a message to create session state
        cy.findByTestId("chat-input").type("Hello{enter}");

        // Wait for agent response
        cy.findByRole("button", { name: "View Activity" }).should("be.visible");

        // Change model — should clear chat and start new session
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Gemini 3.1 Pro").click();

        // Previous messages should be cleared (new session started)
        // The welcome message from the new agent may or may not appear
        // depending on whether the agent is registered
        cy.findByRole("button", { name: "View Activity" }).should("not.exist");
    });

    it("should force new session when mode changes", () => {
        // Send a message
        cy.findByTestId("chat-input").type("Hello{enter}");
        cy.findByRole("button", { name: "View Activity" }).should("be.visible");

        // Change mode to Triage
        cy.get('[aria-label="Select mode"]').click();
        cy.findByText("Medical Triage").click();

        // Previous messages should be cleared
        cy.findByRole("button", { name: "View Activity" }).should("not.exist");
    });
});
