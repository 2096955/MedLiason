import { describe, it, expect } from "vitest";
import {
    CITATION_PATTERN,
    MULTI_CITATION_PATTERN,
    parseCitations,
    getCitationTooltip,
    removeCitationMarkers,
} from "../citations";
import type { Citation } from "../citations";

describe("KG citations", () => {
    it("CITATION_PATTERN matches [[cite:kg0r0]]", () => {
        const text = "Evidence shows [[cite:kg0r0]] that...";
        CITATION_PATTERN.lastIndex = 0;
        const match = CITATION_PATTERN.exec(text);
        expect(match).not.toBeNull();
        expect(match![1]).toBe("kg0r0");
    });

    it("CITATION_PATTERN matches [[cite:kg0r12]]", () => {
        const text = "[[cite:kg0r12]]";
        CITATION_PATTERN.lastIndex = 0;
        const match = CITATION_PATTERN.exec(text);
        expect(match![1]).toBe("kg0r12");
    });

    it("parseCitations parses kg0r0 with type kg", () => {
        const text = "Result [[cite:kg0r0]] here";
        const citations = parseCitations(text);
        expect(citations).toHaveLength(1);
        expect(citations[0].type).toBe("kg");
        expect(citations[0].sourceId).toBe(0);
        expect(citations[0].citationId).toBe("kg0r0");
    });

    it("parseCitations handles mixed kg + s citations", () => {
        const text = "KG [[cite:kg0r0]] and web [[cite:s0r1]] sources";
        const citations = parseCitations(text);
        expect(citations).toHaveLength(2);
        expect(citations[0].type).toBe("kg");
        expect(citations[1].type).toBe("search");
    });

    it("MULTI_CITATION_PATTERN matches [[cite:kg0r0, s0r1]]", () => {
        const text = "[[cite:kg0r0, s0r1]]";
        MULTI_CITATION_PATTERN.lastIndex = 0;
        const match = MULTI_CITATION_PATTERN.exec(text);
        expect(match).not.toBeNull();
    });

    it("getCitationTooltip returns KG entity format", () => {
        const citation: Citation = {
            marker: "[[cite:kg0r0]]",
            type: "kg",
            sourceId: 0,
            position: 0,
            citationId: "kg0r0",
            source: {
                citationId: "kg0r0",
                contentPreview: "A gene",
                relevanceScore: 1.0,
                metadata: { labels: ["Gene"], title: "BRCA1" },
            } as any,
        };
        const tooltip = getCitationTooltip(citation);
        expect(tooltip).toContain("Knowledge Graph");
        expect(tooltip).toContain("BRCA1");
    });

    it("getCitationTooltip returns URL for Study nodes", () => {
        const citation: Citation = {
            marker: "[[cite:kg0r0]]",
            type: "kg",
            sourceId: 0,
            position: 0,
            citationId: "kg0r0",
            source: {
                citationId: "kg0r0",
                contentPreview: "RCT",
                relevanceScore: 1.0,
                sourceUrl: "https://pubmed.ncbi.nlm.nih.gov/12345/",
                metadata: { title: "Study of Drug X" },
            } as any,
        };
        const tooltip = getCitationTooltip(citation);
        expect(tooltip).toContain("Study of Drug X");
        expect(tooltip).toContain("pubmed");
    });

    it("removeCitationMarkers strips kg citations", () => {
        const text = "Before [[cite:kg0r0]] after";
        const cleaned = removeCitationMarkers(text);
        expect(cleaned).toBe("Before  after");
    });
});
