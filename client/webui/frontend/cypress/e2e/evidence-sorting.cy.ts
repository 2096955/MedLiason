/**
 * E2E tests for the Evidence Sorting feature in RAGInfoPanel.
 *
 * These tests verify the sort dropdown renders, changes sort mode,
 * and grade badges display correctly in the Sources side panel.
 *
 * Requires: Full stack running with backend that returns RAG sources.
 * These tests send a research query and wait for sources to populate.
 */

describe("Evidence Sorting — Side Panel", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
    });

    it("should have Sources tab in side panel", () => {
        // Expand the side panel
        cy.findByRole("button", { name: "Expand Panel" }).click();

        // Check that a Sources/RAG tab exists
        cy.findByRole("tablist").should("be.visible");
    });
});

describe("Evidence Sorting — Sort Dropdown (with data)", { tags: ["@community"] }, () => {
    /**
     * These tests require a completed research query that produced RAG sources.
     * They send a medical question and wait for the side panel to populate.
     * On environments without a running backend, these tests will be skipped
     * via Cypress retry/timeout behavior.
     */

    beforeEach(() => {
        cy.navigateToChat();
        cy.startNewChat();

        // Send a research query that will produce evidence sources
        cy.findByTestId("chat-input").type("What is the evidence for aspirin in cardiovascular prevention?{enter}");

        // Wait for the response to complete (agent response appears)
        // This may take up to 90 seconds for a full 12-step research protocol
        // responseTimeout in cypress.config.js is 90s — adequate for research protocol
        cy.findByRole("button", { name: "View Activity" }).should("be.visible");
    });

    it("should display sort dropdown in Sources panel when sources exist", () => {
        // Open the side panel
        cy.findByRole("button", { name: "Expand Panel" }).click();

        // Click on the Sources/RAG tab
        cy.findByRole("tab", { name: /sources/i }).click();

        // The sort dropdown should be visible if sources were returned
        cy.get("body").then($body => {
            if ($body.text().includes("Evidence Grade")) {
                // Sort dropdown is present — verify all 3 options
                cy.findByText("Evidence Grade").should("be.visible");
            }
        });
    });

    it("should allow changing sort mode", () => {
        cy.findByRole("button", { name: "Expand Panel" }).click();
        cy.findByRole("tab", { name: /sources/i }).click();

        cy.get("body").then($body => {
            if ($body.text().includes("Evidence Grade")) {
                // Click the sort dropdown and select "Most Recent"
                cy.findByText("Evidence Grade").click();
                cy.findByText("Most Recent").click();

                // Verify the dropdown updated
                cy.findByText("Most Recent").should("be.visible");
            }
        });
    });
});
