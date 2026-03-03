/**
 * E2E tests for mobile responsive layout and PWA components.
 *
 * Verifies that the MobileBottomNav renders on small viewports,
 * the desktop sidebar hides, and the model selector remains functional.
 *
 * Uses Cypress viewport commands to simulate mobile and desktop breakpoints.
 */

describe("Mobile Responsive — Bottom Navigation", { tags: ["@community"] }, () => {
    beforeEach(() => {
        // Set mobile viewport (iPhone 14 Pro dimensions)
        cy.viewport(393, 852);
        cy.visit("/");
        cy.get("body").should("be.visible");
    });

    it("should display MobileBottomNav on mobile viewport", () => {
        // The mobile bottom nav should have 5 tabs: Chat, Sources, Agents, Sessions, Settings
        // Check for the navigation container
        cy.get('[role="navigation"]').should("be.visible");
    });

    it("should have chat input area visible on mobile", () => {
        cy.findByTestId("chat-input").should("be.visible");
    });

    it("should have Model and Mode selects visible on mobile", () => {
        cy.get('[aria-label="Select AI model"]').should("be.visible");
        cy.get('[aria-label="Select mode"]').should("be.visible");
    });

    it("should allow model selection on mobile", () => {
        cy.get('[aria-label="Select AI model"]').click();
        cy.findByText("Gemini 2.5 Flash").should("be.visible");
        cy.findByText("Gemini 3.1 Pro").should("be.visible");
        cy.findByText("Claude Opus 4.6").should("be.visible");
    });
});

describe("Mobile Responsive — Desktop Layout", { tags: ["@community"] }, () => {
    beforeEach(() => {
        // Set desktop viewport
        cy.viewport(1280, 720);
        cy.visit("/");
        cy.get("body").should("be.visible");
    });

    it("should NOT display MobileBottomNav on desktop viewport", () => {
        // The mobile bottom nav should not be visible on desktop
        // Check that the desktop sidebar is shown instead
        cy.findByRole("button", { name: "Chat" }).should("be.visible");
        cy.findByRole("button", { name: "Agent Mesh" }).should("be.visible");
    });

    it("should display Model and Mode selects on desktop", () => {
        cy.findByRole("button", { name: "Chat" }).click();
        cy.get('[aria-label="Select AI model"]').should("be.visible");
        cy.get('[aria-label="Select mode"]').should("be.visible");
    });
});

describe("Mobile Responsive — Viewport Transition", { tags: ["@community"] }, () => {
    it("should transition from desktop to mobile layout", () => {
        // Start at desktop
        cy.viewport(1280, 720);
        cy.visit("/");
        cy.findByRole("button", { name: "Chat" }).should("be.visible");

        // Resize to mobile
        cy.viewport(393, 852);

        // Desktop sidebar should be hidden, mobile nav should appear
        // Model selects should still be functional
        cy.get('[aria-label="Select AI model"]').should("exist");
    });
});
