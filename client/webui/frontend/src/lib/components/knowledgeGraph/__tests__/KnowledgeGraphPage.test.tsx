/// <reference types="@testing-library/jest-dom" />
import { render, screen, waitFor } from "@testing-library/react";
import { describe, test, expect, beforeEach, vi } from "vitest";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

// Mock react-router-dom
const mockSearchParams = new URLSearchParams();
vi.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams],
    useNavigate: () => vi.fn(),
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

    test("renders the page with title", async () => {
        render(<KnowledgeGraphPage />);

        expect(screen.getByText("Knowledge Graph")).toBeInTheDocument();
    });

    test("shows empty state when no data", async () => {
        render(<KnowledgeGraphPage />);

        await waitFor(() => {
            expect(screen.getByText("No graph data available")).toBeInTheDocument();
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

    test("renders entity type filter buttons", async () => {
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

    test("shows session ID when ?session param provided", async () => {
        mockSearchParams.set("session", "abc123def456789xyz");

        render(<KnowledgeGraphPage />);

        await waitFor(() => {
            expect(screen.getByText("abc123def456...")).toBeInTheDocument();
        });
    });

    test("renders GraphCanvas when data is available", async () => {
        mockFetch.mockImplementation((url: string) => {
            if (url.includes("/api/v1/graph/stats")) {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({ total_nodes: 2, total_edges: 1, node_counts: {}, edge_counts: {} }),
                });
            }
            if (url.includes("/api/v1/graph/explore")) {
                return Promise.resolve({
                    ok: true,
                    json: () =>
                        Promise.resolve({
                            nodes: [
                                { id: "1", labels: ["Disease"], name: "Diabetes", properties: {} },
                                { id: "2", labels: ["Drug"], name: "Metformin", properties: {} },
                            ],
                            edges: [{ id: "e1", source: "1", target: "2", label: "FOUND" }],
                        }),
                });
            }
            return Promise.resolve({ ok: false, status: 404 });
        });

        render(<KnowledgeGraphPage />);

        await waitFor(() => {
            // GraphCanvas renders SVG with column headers
            expect(screen.getByText("SESSIONS")).toBeInTheDocument();
            expect(screen.getByText("SPECIALISTS")).toBeInTheDocument();
            expect(screen.getByText("SHARED ENTITIES")).toBeInTheDocument();
            expect(screen.getByText("STUDIES")).toBeInTheDocument();
        });
    });
});
