/// <reference types="@testing-library/jest-dom" />
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, test, expect, beforeEach, vi } from "vitest";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

// Mock react-router-dom
const mockSearchParams = new URLSearchParams();
vi.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams],
    useNavigate: () => vi.fn(),
}));

// Mock cytoscape
vi.mock("cytoscape", () => {
    const mockCy = {
        on: vi.fn(),
        nodes: vi.fn(() => ({ removeClass: vi.fn() })),
        getElementById: vi.fn(() => ({ length: 0, addClass: vi.fn() })),
        destroy: vi.fn(),
    };
    const cytoscapeFn = vi.fn(() => mockCy);
    (cytoscapeFn as unknown as Record<string, unknown>).use = vi.fn();
    return { default: cytoscapeFn };
});

vi.mock("cytoscape-cose-bilkent", () => ({
    default: vi.fn(),
}));

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

// Dynamic import for lazy component — import the component directly for testing
import KnowledgeGraphPage from "../KnowledgeGraphPage";

describe("KnowledgeGraphPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockSearchParams.delete("session");

        // Default: stats returns empty, explore returns empty
        mockFetch.mockImplementation((url: string) => {
            if (url.includes("/api/v1/graph/stats")) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ total_nodes: 0, total_edges: 0, node_counts: {}, edge_counts: {} }),
                });
            }
            if (url.includes("/api/v1/graph/explore")) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ nodes: [], edges: [] }),
                });
            }
            if (url.includes("/api/v1/graph/session")) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ nodes: [], edges: [] }),
                });
            }
            return Promise.resolve({ ok: false, status: 404 });
        });
    });

    test("renders the page with title and tabs", async () => {
        render(<KnowledgeGraphPage />);

        expect(screen.getByText("Knowledge Graph")).toBeInTheDocument();
        expect(screen.getByText("Session")).toBeInTheDocument();
        expect(screen.getByText("Knowledge Base")).toBeInTheDocument();
    });

    test("shows empty state for explore tab when no data", async () => {
        render(<KnowledgeGraphPage />);

        await waitFor(() => {
            expect(screen.getByText("No entities found")).toBeInTheDocument();
        });
    });

    test("shows empty state for session tab with no session selected", async () => {
        render(<KnowledgeGraphPage />);

        // Click the Session tab
        const sessionTab = screen.getByText("Session");
        await userEvent.click(sessionTab);

        await waitFor(() => {
            expect(screen.getByText("No session selected")).toBeInTheDocument();
        });
    });

    test("fetches stats on mount", async () => {
        mockFetch.mockImplementation((url: string) => {
            if (url.includes("/api/v1/graph/stats")) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ total_nodes: 42, total_edges: 78, node_counts: {}, edge_counts: {} }),
                });
            }
            return Promise.resolve({
                ok: true,
                json: () => Promise.resolve({ nodes: [], edges: [] }),
            });
        });

        render(<KnowledgeGraphPage />);

        await waitFor(() => {
            expect(screen.getByText("42 nodes")).toBeInTheDocument();
            expect(screen.getByText("78 edges")).toBeInTheDocument();
        });
    });

    test("renders entity type filter buttons in explore tab", async () => {
        render(<KnowledgeGraphPage />);

        expect(screen.getByText("Disease")).toBeInTheDocument();
        expect(screen.getByText("Drug")).toBeInTheDocument();
        expect(screen.getByText("Gene")).toBeInTheDocument();
        expect(screen.getByText("Study")).toBeInTheDocument();
        expect(screen.getByText("All")).toBeInTheDocument();
    });

    test("renders the NLQ query bar", async () => {
        render(<KnowledgeGraphPage />);

        expect(screen.getByPlaceholderText("Ask about the knowledge graph...")).toBeInTheDocument();
    });

    test("renders refresh button", async () => {
        render(<KnowledgeGraphPage />);

        expect(screen.getByText("Refresh")).toBeInTheDocument();
    });
});
