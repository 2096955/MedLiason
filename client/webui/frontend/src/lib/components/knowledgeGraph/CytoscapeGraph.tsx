import React, { useEffect, useRef, useCallback, useState } from "react";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";

// Register layout extension once
cytoscape.use(coseBilkent);

// ── Node type styling ────────────────────────────────────────────────
const NODE_COLORS: Record<string, string> = {
    Disease: "#ef4444",
    Drug: "#3b82f6",
    Gene: "#22c55e",
    Study: "#f59e0b",
    Session: "#a855f7",
    Specialist: "#06b6d4",
};

const NODE_SHAPES: Record<string, string> = {
    Disease: "ellipse",
    Drug: "hexagon",
    Gene: "diamond",
    Study: "round-rectangle",
    Session: "star",
    Specialist: "octagon",
};

const DEFAULT_NODE_COLOR = "#6b7280";

function getNodeColor(label: string): string {
    return NODE_COLORS[label] || DEFAULT_NODE_COLOR;
}

function getNodeShape(label: string): string {
    return NODE_SHAPES[label] || "ellipse";
}

/** Degree-scaled node size: sqrt scaling clamped to 28-72px */
function getNodeSize(degree: number): number {
    return Math.min(72, Math.max(28, 28 + Math.sqrt(degree) * 10));
}

// ── Component ────────────────────────────────────────────────────────

interface CytoscapeElement {
    data: Record<string, unknown>;
    group?: "nodes" | "edges";
}

interface CytoscapeGraphProps {
    elements: CytoscapeElement[];
    onNodeClick?: (nodeId: string, nodeData: Record<string, unknown>) => void;
    highlightedNodes?: string[];
    layout?: string;
}

interface TooltipState {
    x: number;
    y: number;
    label: string;
    type: string;
}

const CytoscapeGraph: React.FC<CytoscapeGraphProps> = ({ elements, onNodeClick, highlightedNodes, layout = "cose-bilkent" }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<cytoscape.Core | null>(null);
    const [tooltip, setTooltip] = useState<TooltipState | null>(null);

    const handleNodeClick = useCallback(
        (nodeId: string, nodeData: Record<string, unknown>) => {
            onNodeClick?.(nodeId, nodeData);
        },
        [onNodeClick]
    );

    useEffect(() => {
        if (!containerRef.current) return;

        const cy = cytoscape({
            container: containerRef.current,
            elements: elements as cytoscape.ElementDefinition[],
            style: [
                // ── Base node style ──────────────────────────────
                {
                    selector: "node",
                    style: {
                        label: "data(label)",
                        "text-valign": "bottom",
                        "text-halign": "center",
                        "font-size": "10px",
                        "font-weight": "normal",
                        "text-margin-y": 8,
                        color: "#f1f5f9",
                        // Dark pill behind label for readability
                        "text-outline-width": 0,
                        "text-background-opacity": 1,
                        "text-background-color": "rgba(15,23,42,0.85)",
                        "text-background-shape": "roundrectangle",
                        "text-background-padding": "3px",
                        "text-max-width": "80px",
                        "text-wrap": "ellipsis",
                        // Degree-scaled sizing
                        width: (ele: cytoscape.NodeSingular) => getNodeSize((ele.data("degree") as number) ?? 0),
                        height: (ele: cytoscape.NodeSingular) => getNodeSize((ele.data("degree") as number) ?? 0),
                        // Color and shape by type
                        "background-color": (ele: cytoscape.NodeSingular) => getNodeColor(ele.data("nodeLabel") as string),
                        shape: (ele: cytoscape.NodeSingular) => getNodeShape(ele.data("nodeLabel") as string) as cytoscape.Css.NodeShape,
                        // Type-matched border
                        "border-width": 2,
                        "border-color": (ele: cytoscape.NodeSingular) => getNodeColor(ele.data("nodeLabel") as string),
                        "border-opacity": 0.5,
                    },
                },
                // ── Highlighted node ─────────────────────────────
                {
                    selector: "node.highlighted",
                    style: {
                        "border-width": 3,
                        "border-color": "#fbbf24",
                        "border-opacity": 1,
                    },
                },
                // ── Base edge (labels hidden by default) ─────────
                {
                    selector: "edge",
                    style: {
                        label: "",
                        width: 1.5,
                        "line-color": "#334155",
                        "target-arrow-color": "#334155",
                        "target-arrow-shape": "triangle",
                        "curve-style": "unbundled-bezier",
                        opacity: 0.6,
                    },
                },
                // ── Edge hover/selected (show label) ─────────────
                {
                    selector: "edge:selected, edge.hovered",
                    style: {
                        label: "data(label)",
                        "font-size": "10px",
                        "font-weight": "bold",
                        color: "#94a3b8",
                        "text-background-opacity": 1,
                        "text-background-color": "rgba(15,23,42,0.9)",
                        "text-background-shape": "roundrectangle",
                        "text-background-padding": "3px",
                        "text-rotation": "autorotate",
                        "line-color": "#64748b",
                        "target-arrow-color": "#64748b",
                        width: 2.5,
                        opacity: 1,
                    },
                },
            ],
            layout: {
                name: layout,
                animate: false,
                nodeDimensionsIncludeLabels: true,
                idealEdgeLength: 160,
                nodeRepulsion: 12000,
                gravity: 0.15,
                numIter: 2500,
                randomize: false,
                fit: true,
                padding: 40,
            } as cytoscape.LayoutOptions,
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
        });

        // ── P2-1: Betweenness centrality sizing (post-layout) ─────
        // Replaces degree sizing for graphs under 300 elements.
        // Bridge nodes (connecting clusters) get sized larger.
        const eleCount = cy.elements().length;
        if (eleCount > 0 && eleCount < 300) {
            try {
                const bc = cy.elements().betweennessCentrality({ directed: false });
                cy.nodes().forEach((node) => {
                    const score = bc.betweennessNormalized(node);
                    const degree = (node.data("degree") as number) ?? 0;
                    // Blend: 60% betweenness + 40% degree for balanced sizing
                    const blended = score * 0.6 + Math.min(degree / 10, 1) * 0.4;
                    const size = Math.min(80, Math.max(28, 28 + blended * 52));
                    node.style({ width: size, height: size });
                });
            } catch {
                // Betweenness can fail on disconnected graphs — keep degree sizing
            }
        }

        // ── P2-2: Community detection coloring (border rings) ────
        // Markov clustering detects communities; border color signals cluster.
        // Node fill stays as entity-type color for semantic meaning.
        const CLUSTER_COLORS = ["#6366f1", "#f43f5e", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6"];
        if (eleCount > 4 && eleCount < 300) {
            try {
                const clusters = cy.elements().markovClustering({}) as unknown as cytoscape.Collection[];
                clusters.forEach((cluster, i) => {
                    const color = CLUSTER_COLORS[i % CLUSTER_COLORS.length];
                    (cluster as cytoscape.Collection).nodes().forEach((node) => {
                        node.style("border-color", color);
                        node.style("border-width", 3);
                        node.style("border-opacity", 0.7);
                    });
                });
            } catch {
                // Clustering can fail on trivial graphs — keep type-matched borders
            }
        }

        // Node click
        cy.on("tap", "node", (evt) => {
            const node = evt.target;
            handleNodeClick(node.id(), node.data());
        });

        // Edge hover — show/hide label
        cy.on("mouseover", "edge", (evt) => evt.target.addClass("hovered"));
        cy.on("mouseout", "edge", (evt) => evt.target.removeClass("hovered"));

        // Node hover — tooltip
        cy.on("mouseover", "node", (evt) => {
            const node = evt.target;
            const pos = evt.renderedPosition;
            const container = containerRef.current;
            if (!container) return;
            const rect = container.getBoundingClientRect();
            setTooltip({
                x: rect.left + pos.x,
                y: rect.top + pos.y - 8,
                label: node.data("label") as string,
                type: node.data("nodeLabel") as string,
            });
        });
        cy.on("mouseout", "node", () => setTooltip(null));

        cyRef.current = cy;

        return () => {
            cy.destroy();
            cyRef.current = null;
        };
    }, [elements, layout, handleNodeClick]);

    // Handle highlighted nodes with animation (P2-5)
    useEffect(() => {
        const cy = cyRef.current;
        if (!cy) return;

        if (highlightedNodes && highlightedNodes.length > 0) {
            // Fade non-highlighted nodes
            cy.nodes().forEach((node) => {
                if (highlightedNodes.includes(node.id())) {
                    node.addClass("highlighted");
                    node.animate({
                        style: { "border-width": 4, "border-color": "#fbbf24", "border-opacity": 1 },
                        duration: 300,
                        easing: "ease-out-cubic" as cytoscape.Css.TransitionTimingFunction,
                    });
                } else {
                    node.animate({
                        style: { opacity: 0.25 },
                        duration: 300,
                        easing: "ease-out-cubic" as cytoscape.Css.TransitionTimingFunction,
                    });
                }
            });
        } else {
            // Restore all nodes
            cy.nodes().removeClass("highlighted");
            cy.nodes().animate({
                style: { opacity: 1 },
                duration: 300,
                easing: "ease-out-cubic" as cytoscape.Css.TransitionTimingFunction,
            });
        }
    }, [highlightedNodes]);

    const handleFit = useCallback(() => {
        cyRef.current?.fit(undefined, 40);
    }, []);

    return (
        <>
            <div
                ref={containerRef}
                className="h-full w-full rounded-lg border border-border"
                style={{ background: "#080f1a" }}
            />
            {/* P2-4: Fit-to-screen button */}
            <button
                onClick={handleFit}
                className="absolute bottom-4 right-4 z-10 rounded-md border border-border bg-background/90 px-2 py-1 text-xs text-muted-foreground backdrop-blur hover:text-foreground"
            >
                Fit
            </button>
            {tooltip && (
                <div
                    className="pointer-events-none fixed z-50 rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
                    style={{ left: tooltip.x, top: tooltip.y, transform: "translate(-50%, -100%)" }}
                >
                    <span className="font-medium">{tooltip.label}</span>
                    <span className="ml-1.5 opacity-60">{tooltip.type}</span>
                </div>
            )}
        </>
    );
};

export default CytoscapeGraph;
