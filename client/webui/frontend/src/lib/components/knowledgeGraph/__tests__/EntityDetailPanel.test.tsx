/// <reference types="@testing-library/jest-dom" />
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, test, expect, vi } from "vitest";
import * as matchers from "@testing-library/jest-dom/matchers";

expect.extend(matchers);

import EntityDetailPanel from "../EntityDetailPanel";
import type { GraphNode } from "@/lib/types";

function makeNode(overrides: Partial<GraphNode> & { labels: string[]; name: string }): GraphNode {
    return {
        id: `node-${overrides.name}`,
        description: "",
        properties: {},
        ...overrides,
    };
}

describe("EntityDetailPanel", () => {
    test("renders Study node with PubMed link", () => {
        const node = makeNode({
            labels: ["Study"],
            name: "Study 38901234",
            properties: { pmid: "38901234", title: "RCT of Drug X" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        expect(screen.getByText("Study 38901234")).toBeInTheDocument();
        expect(screen.getByText("View on PubMed")).toBeInTheDocument();
        expect(screen.getByText("View on PubMed")).toHaveAttribute(
            "href",
            "https://pubmed.ncbi.nlm.nih.gov/38901234/"
        );
    });

    test("renders Study node with ClinicalTrials link", () => {
        const node = makeNode({
            labels: ["Study"],
            name: "NCT06123456",
            properties: { nct_id: "NCT06123456" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        expect(screen.getByText("View on ClinicalTrials.gov")).toBeInTheDocument();
        expect(screen.getByText("View on ClinicalTrials.gov")).toHaveAttribute(
            "href",
            "https://clinicaltrials.gov/study/NCT06123456"
        );
    });

    test("onViewSource fires with pmid for Study nodes", () => {
        const onViewSource = vi.fn();
        const node = makeNode({
            labels: ["Study"],
            name: "Study 12345",
            properties: { pmid: "12345" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} onViewSource={onViewSource} />);

        fireEvent.click(screen.getByText("View in Sources"));
        expect(onViewSource).toHaveBeenCalledWith({ type: "pmid", value: "12345" });
    });

    test("onViewSource fires with nct_id when no pmid", () => {
        const onViewSource = vi.fn();
        const node = makeNode({
            labels: ["Study"],
            name: "Trial",
            properties: { nct_id: "NCT00001234" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} onViewSource={onViewSource} />);

        fireEvent.click(screen.getByText("View in Sources"));
        expect(onViewSource).toHaveBeenCalledWith({ type: "nct_id", value: "NCT00001234" });
    });

    test("View in Sources button hidden when no onViewSource prop", () => {
        const node = makeNode({
            labels: ["Study"],
            name: "Study",
            properties: { pmid: "12345" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        expect(screen.queryByText("View in Sources")).not.toBeInTheDocument();
    });

    test("onSearchSources fires for Disease entity node", () => {
        const onSearchSources = vi.fn();
        const node = makeNode({
            labels: ["Disease"],
            name: "breast cancer",
            description: "A common malignancy",
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} onSearchSources={onSearchSources} />);

        fireEvent.click(screen.getByText("Search Sources"));
        expect(onSearchSources).toHaveBeenCalledWith("breast cancer");
    });

    test("onSearchSources fires for Drug entity node", () => {
        const onSearchSources = vi.fn();
        const node = makeNode({
            labels: ["Drug"],
            name: "bevacizumab",
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} onSearchSources={onSearchSources} />);

        fireEvent.click(screen.getByText("Search Sources"));
        expect(onSearchSources).toHaveBeenCalledWith("bevacizumab");
    });

    test("Search Sources hidden for Study nodes with identifiers", () => {
        const onSearchSources = vi.fn();
        const node = makeNode({
            labels: ["Study"],
            name: "Study 12345",
            properties: { pmid: "12345" },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} onSearchSources={onSearchSources} />);

        // Study nodes with identifiers should show "View in Sources" context, not "Search Sources"
        expect(screen.queryByText("Search Sources")).not.toBeInTheDocument();
    });

    test("renders description when present", () => {
        const node = makeNode({
            labels: ["Gene"],
            name: "BRCA1",
            description: "Tumor suppressor gene involved in DNA repair",
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        expect(screen.getByText("Tumor suppressor gene involved in DNA repair")).toBeInTheDocument();
    });

    test("shows partial stub indicator", () => {
        const node = makeNode({
            labels: ["Study"],
            name: "Stub study",
            properties: { partial: true },
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        expect(screen.getByText(/Stub — referenced but not directly retrieved/)).toBeInTheDocument();
    });

    test("renders label badges with correct colors", () => {
        const node = makeNode({
            labels: ["Disease"],
            name: "cancer",
        });

        render(<EntityDetailPanel node={node} onClose={vi.fn()} />);

        const badge = screen.getByText("Disease");
        expect(badge).toBeInTheDocument();
        expect(badge.className).toContain("text-red-400");
    });
});
