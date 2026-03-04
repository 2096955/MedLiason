import React, { useEffect, useRef, useCallback } from "react";
import cytoscape from "cytoscape";
import coseBilkent from "cytoscape-cose-bilkent";

// Register layout extension once
cytoscape.use(coseBilkent);

const NODE_COLORS: Record<string, string> = {
    Disease: "#ef4444",
    Drug: "#3b82f6",
    Gene: "#22c55e",
    Study: "#f59e0b",
    Session: "#a855f7",
    Specialist: "#06b6d4",
};

const DEFAULT_NODE_COLOR = "#6b7280";

function getNodeColor(label: string): string {
    return NODE_COLORS[label] || DEFAULT_NODE_COLOR;
}

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

const CytoscapeGraph: React.FC<CytoscapeGraphProps> = ({ elements, onNodeClick, highlightedNodes, layout = "cose-bilkent" }) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<cytoscape.Core | null>(null);

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
                {
                    selector: "node",
                    style: {
                        label: "data(label)",
                        "text-valign": "bottom",
                        "text-halign": "center",
                        "font-size": "11px",
                        "text-margin-y": 6,
                        color: "#d1d5db",
                        width: 36,
                        height: 36,
                        "background-color": (ele: cytoscape.NodeSingular) => {
                            const nodeLabel = ele.data("nodeLabel") as string;
                            return getNodeColor(nodeLabel);
                        },
                        "border-width": 2,
                        "border-color": "#374151",
                        "text-outline-width": 2,
                        "text-outline-color": "#1f2937",
                    },
                },
                {
                    selector: "node.highlighted",
                    style: {
                        "border-width": 4,
                        "border-color": "#fbbf24",
                        width: 44,
                        height: 44,
                    },
                },
                {
                    selector: "edge",
                    style: {
                        label: "data(label)",
                        "font-size": "9px",
                        color: "#9ca3af",
                        width: 2,
                        "line-color": "#4b5563",
                        "target-arrow-color": "#4b5563",
                        "target-arrow-shape": "triangle",
                        "curve-style": "bezier",
                        "text-rotation": "autorotate",
                        "text-outline-width": 2,
                        "text-outline-color": "#1f2937",
                    },
                },
            ],
            layout: {
                name: layout,
                animate: false,
                nodeDimensionsIncludeLabels: true,
                idealEdgeLength: 120,
                nodeRepulsion: 8000,
                gravity: 0.25,
            } as cytoscape.LayoutOptions,
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
        });

        cy.on("tap", "node", (evt) => {
            const node = evt.target;
            handleNodeClick(node.id(), node.data());
        });

        cyRef.current = cy;

        return () => {
            cy.destroy();
            cyRef.current = null;
        };
    }, [elements, layout, handleNodeClick]);

    // Handle highlighted nodes
    useEffect(() => {
        const cy = cyRef.current;
        if (!cy) return;

        cy.nodes().removeClass("highlighted");
        if (highlightedNodes && highlightedNodes.length > 0) {
            highlightedNodes.forEach((id) => {
                const node = cy.getElementById(id);
                if (node.length > 0) {
                    node.addClass("highlighted");
                }
            });
        }
    }, [highlightedNodes]);

    return <div ref={containerRef} className="h-full w-full rounded-lg border border-border bg-background" />;
};

export default CytoscapeGraph;
