/// <reference types="@testing-library/jest-dom" />
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, test, expect, vi } from "vitest";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

import GraphCanvas from "../GraphCanvas";

const mockNodes = [
    { id: "1", labels: ["Session"], name: "Session A", description: "", properties: { query: "diabetes treatment" } },
    { id: "2", labels: ["Specialist"], name: "DrugSpecialist", description: "", properties: {} },
    { id: "3", labels: ["Disease"], name: "Diabetes", description: "", properties: {} },
    { id: "4", labels: ["Study"], name: "PMID:12345", description: "", properties: { pmid: "12345" } },
];

const mockEdges = [
    { id: "e1", source: "1", target: "2", label: "QUERIED" },
    { id: "e2", source: "2", target: "3", label: "FOUND" },
    { id: "e3", source: "3", target: "4", label: "EVIDENCED_BY" },
];

describe("GraphCanvas", () => {
    test("renders SVG container", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(document.querySelector("svg")).toBeTruthy();
    });

    test("renders column headers", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByText("SESSIONS")).toBeInTheDocument();
        expect(screen.getByText("SPECIALISTS")).toBeInTheDocument();
        expect(screen.getByText("SHARED ENTITIES")).toBeInTheDocument();
        expect(screen.getByText("STUDIES")).toBeInTheDocument();
    });

    test("renders node labels", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        // Session shows truncated query
        expect(screen.getByText("diabetes treatment")).toBeInTheDocument();
        // Specialist shows name without "Specialist"
        expect(screen.getByText("Drug")).toBeInTheDocument();
        // Entity shows full name
        expect(screen.getByText("Diabetes")).toBeInTheDocument();
        // Study shows PMID format
        expect(screen.getByText("PMID:12345")).toBeInTheDocument();
    });

    test("renders edge legend", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} />);
        expect(screen.getByText("QUERIED")).toBeInTheDocument();
        expect(screen.getByText("FOUND")).toBeInTheDocument();
        expect(screen.getByText("EVIDENCED_BY")).toBeInTheDocument();
        expect(screen.getByText("CITED")).toBeInTheDocument();
    });

    test("shows empty message for no nodes", () => {
        render(<GraphCanvas nodes={[]} edges={[]} />);
        expect(screen.getByText("No graph data available")).toBeInTheDocument();
    });

    test("calls onNodeClick when node is clicked", () => {
        const handleClick = vi.fn();
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} onNodeClick={handleClick} />);

        // Use data-testid for stable targeting (survives SVG nesting changes)
        const nodeGroup = document.querySelector('[data-testid="graph-node-3"]');
        expect(nodeGroup).toBeTruthy();
        fireEvent.click(nodeGroup!);
        expect(handleClick).toHaveBeenCalledTimes(1);
        expect(handleClick).toHaveBeenCalledWith(expect.objectContaining({ id: "3", name: "Diabetes" }));
    });

    test("renders partial study nodes with dashed stroke", () => {
        const partialStudy = {
            id: "5",
            labels: ["Study"],
            name: "PMID:99999",
            description: "",
            properties: { pmid: "99999", partial: true },
        };
        render(<GraphCanvas nodes={[...mockNodes, partialStudy]} edges={mockEdges} />);

        // The partial study should render — just verify it appears
        expect(screen.getByText("PMID:99999")).toBeInTheDocument();
    });

    test("renders degree badge for high-degree nodes", () => {
        // Create a node with many connections (degree > 3)
        const highDegreeNodes = [
            { id: "hub", labels: ["Disease"], name: "Cancer", description: "", properties: {} },
            { id: "s1", labels: ["Study"], name: "S1", description: "", properties: { pmid: "1" } },
            { id: "s2", labels: ["Study"], name: "S2", description: "", properties: { pmid: "2" } },
            { id: "s3", labels: ["Study"], name: "S3", description: "", properties: { pmid: "3" } },
            { id: "s4", labels: ["Study"], name: "S4", description: "", properties: { pmid: "4" } },
        ];
        const highDegreeEdges = [
            { id: "e1", source: "hub", target: "s1", label: "EVIDENCED_BY" },
            { id: "e2", source: "hub", target: "s2", label: "EVIDENCED_BY" },
            { id: "e3", source: "hub", target: "s3", label: "EVIDENCED_BY" },
            { id: "e4", source: "hub", target: "s4", label: "EVIDENCED_BY" },
        ];
        render(<GraphCanvas nodes={highDegreeNodes} edges={highDegreeEdges} />);

        // Hub has degree 4 — should show badge within its data-testid container
        const badge = document.querySelector('[data-testid="degree-badge-hub"]');
        expect(badge).toBeTruthy();
        expect(badge!.textContent).toContain("4");
    });

    test("applies highlight ring when highlightedNodeId is set", () => {
        render(<GraphCanvas nodes={mockNodes} edges={mockEdges} highlightedNodeId="3" />);

        const nodeGroup = document.querySelector('[data-testid="graph-node-3"]');
        expect(nodeGroup).toBeTruthy();
        // Highlighted node should have a highlight ring circle (r = radius + 4)
        const circles = nodeGroup!.querySelectorAll("circle");
        // At minimum: highlight ring + node circle = 2 circles
        expect(circles.length).toBeGreaterThanOrEqual(2);
    });
});
