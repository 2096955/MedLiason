import React, { useState, useMemo, useCallback, useRef } from "react";

import type { GraphNode, GraphEdge, GraphEdgeType } from "@/lib/types";

// ── Constants ────────────────────────────────────────────────

const EDGE_STYLES: Record<string, { color: string; dash: string; width: number }> = {
    QUERIED: { color: "#0e7490", dash: "", width: 2 },
    FOUND: { color: "#7c3aed", dash: "", width: 2 },
    EVIDENCED_BY: { color: "#64748b", dash: "6,3", width: 1.5 },
    CITED: { color: "#d97706", dash: "4,4", width: 1.8 },
};

const DEFAULT_EDGE_STYLE = { color: "#94a3b8", dash: "", width: 1 };

const NODE_STYLES: Record<string, { fill: string; stroke: string; text: string }> = {
    Session: { fill: "#7c3aed", stroke: "#a78bfa", text: "#fff" },
    Specialist: { fill: "#f8fafc", stroke: "#0e7490", text: "#0e7490" },
    Disease: { fill: "#f8fafc", stroke: "#64748b", text: "#1e293b" },
    Drug: { fill: "#f8fafc", stroke: "#3b82f6", text: "#1e293b" },
    Gene: { fill: "#f8fafc", stroke: "#16a34a", text: "#1e293b" },
    Study: { fill: "#f8fafc", stroke: "#d97706", text: "#92400e" },
};

const DEFAULT_NODE_STYLE = { fill: "#f8fafc", stroke: "#94a3b8", text: "#334155" };

const COLUMNS = {
    Session: 90,
    Specialist: 310,
    Entity: 570,
    Study: 830,
} as const;

const COLUMN_LABELS = [
    { x: COLUMNS.Session, label: "SESSIONS" },
    { x: COLUMNS.Specialist, label: "SPECIALISTS" },
    { x: COLUMNS.Entity, label: "SHARED ENTITIES" },
    { x: COLUMNS.Study, label: "STUDIES" },
];

const CANVAS_WIDTH = 960;
const CANVAS_PADDING_TOP = 60;
const NODE_SPACING_Y = 70;
const MIN_NODE_RADIUS = 16;

// ── Layout helpers ───────────────────────────────────────────

type ColumnKey = "Session" | "Specialist" | "Entity" | "Study";

function getNodeColumn(labels: string[]): ColumnKey {
    const label = labels[0] || "";
    if (label === "Session") return "Session";
    if (label === "Specialist") return "Specialist";
    if (["Disease", "Drug", "Gene"].includes(label)) return "Entity";
    if (label === "Study") return "Study";
    return "Entity";
}

function getNodeRadius(label: string, degree: number): number {
    if (label === "Session") return 36;
    if (label === "Specialist") return 24;
    if (label === "Study") return MIN_NODE_RADIUS + Math.min(degree * 1.5, 6);
    return 20 + Math.min(degree * 3, 16); // Entity
}

function getNodeLabel(node: GraphNode): string {
    const label = node.labels[0] || "";
    if (label === "Session") {
        const query = (node.properties.query as string) || "";
        return query.length > 30 ? query.slice(0, 30) + "..." : query || node.name;
    }
    if (label === "Specialist") {
        return node.name.replace("Specialist", "").trim() || node.name;
    }
    if (label === "Study") {
        const pmid = node.properties.pmid as string;
        const nctId = node.properties.nct_id as string;
        if (pmid) return `PMID:${pmid}`;
        if (nctId) return nctId;
        return node.name;
    }
    return node.name;
}

interface PositionedNode extends GraphNode {
    x: number;
    y: number;
    radius: number;
    degree: number;
    displayLabel: string;
    column: ColumnKey;
}

function layoutNodes(nodes: GraphNode[], edges: GraphEdge[]): PositionedNode[] {
    // Compute degrees
    const degreeMap: Record<string, number> = {};
    for (const edge of edges) {
        degreeMap[edge.source] = (degreeMap[edge.source] ?? 0) + 1;
        degreeMap[edge.target] = (degreeMap[edge.target] ?? 0) + 1;
    }

    // Group nodes by column
    const columns: Record<ColumnKey, GraphNode[]> = {
        Session: [],
        Specialist: [],
        Entity: [],
        Study: [],
    };
    for (const node of nodes) {
        const col = getNodeColumn(node.labels);
        columns[col].push(node);
    }

    // Build adjacency for weighted-average positioning
    const adjacency: Record<string, string[]> = {};
    for (const edge of edges) {
        if (!adjacency[edge.source]) adjacency[edge.source] = [];
        if (!adjacency[edge.target]) adjacency[edge.target] = [];
        adjacency[edge.source].push(edge.target);
        adjacency[edge.target].push(edge.source);
    }

    const positioned: Record<string, PositionedNode> = {};
    const columnOrder: ColumnKey[] = ["Session", "Specialist", "Entity", "Study"];

    for (const colKey of columnOrder) {
        const colNodes = columns[colKey];
        if (colNodes.length === 0) continue;

        const x = COLUMNS[colKey];

        // For first column or if no connections, space evenly
        const hasPositionedNeighbors = colKey !== "Session" && Object.keys(positioned).length > 0;

        if (hasPositionedNeighbors) {
            // Weighted average Y from connected nodes in previous columns
            const yValues: { node: GraphNode; y: number }[] = [];
            for (const node of colNodes) {
                const neighbors = adjacency[node.id] || [];
                const connectedYs = neighbors
                    .map((nid) => positioned[nid]?.y)
                    .filter((y): y is number => y !== undefined);

                const avgY =
                    connectedYs.length > 0
                        ? connectedYs.reduce((a, b) => a + b, 0) / connectedYs.length
                        : CANVAS_PADDING_TOP + yValues.length * NODE_SPACING_Y;

                yValues.push({ node, y: avgY });
            }

            // Sort by computed Y
            yValues.sort((a, b) => a.y - b.y);

            // Overlap sweep: push apart nodes closer than minGap
            for (let i = 1; i < yValues.length; i++) {
                const minGap = NODE_SPACING_Y;
                if (yValues[i].y - yValues[i - 1].y < minGap) {
                    yValues[i].y = yValues[i - 1].y + minGap;
                }
            }

            for (const { node, y } of yValues) {
                const degree = degreeMap[node.id] ?? 0;
                const nodeLabel = node.labels[0] || "";
                positioned[node.id] = {
                    ...node,
                    x,
                    y,
                    radius: getNodeRadius(nodeLabel, degree),
                    degree,
                    displayLabel: getNodeLabel(node),
                    column: colKey,
                };
            }
        } else {
            // Space evenly
            for (let i = 0; i < colNodes.length; i++) {
                const node = colNodes[i];
                const degree = degreeMap[node.id] ?? 0;
                const nodeLabel = node.labels[0] || "";
                positioned[node.id] = {
                    ...node,
                    x,
                    y: CANVAS_PADDING_TOP + i * NODE_SPACING_Y,
                    radius: getNodeRadius(nodeLabel, degree),
                    degree,
                    displayLabel: getNodeLabel(node),
                    column: colKey,
                };
            }
        }
    }

    return Object.values(positioned);
}

// ── Curved edge path ─────────────────────────────────────────

function edgePath(x1: number, y1: number, x2: number, y2: number): string {
    // Same-column edges (e.g. Study-CITED->Study): curve outward to the right
    if (Math.abs(x1 - x2) < 20) {
        const bulge = 60;
        const midY = (y1 + y2) / 2;
        return `M ${x1} ${y1} C ${x1 + bulge} ${y1}, ${x2 + bulge} ${y2}, ${x2} ${y2}`;
    }
    // Cross-column edges: horizontal bezier
    const midX = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;
}

// ── Component ────────────────────────────────────────────────

interface GraphCanvasProps {
    nodes: GraphNode[];
    edges: GraphEdge[];
    onNodeClick?: (node: GraphNode) => void;
    highlightedNodeId?: string | null;
}

const GraphCanvas: React.FC<GraphCanvasProps> = ({ nodes, edges, onNodeClick, highlightedNodeId }) => {
    const svgRef = useRef<SVGSVGElement>(null);
    const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
    const [hoveredEdgeType, setHoveredEdgeType] = useState<string | null>(null);
    const [dragState, setDragState] = useState<{
        nodeId: string;
        offsetX: number;
        offsetY: number;
    } | null>(null);
    const [dragPositions, setDragPositions] = useState<Record<string, { x: number; y: number }>>({});

    const positioned = useMemo(() => layoutNodes(nodes, edges), [nodes, edges]);

    // Apply drag overrides
    const finalNodes = useMemo(() => {
        return positioned.map((n) => {
            const override = dragPositions[n.id];
            return override ? { ...n, x: override.x, y: override.y } : n;
        });
    }, [positioned, dragPositions]);

    const nodeMap = useMemo(() => {
        const map: Record<string, PositionedNode> = {};
        for (const n of finalNodes) map[n.id] = n;
        return map;
    }, [finalNodes]);

    // Compute canvas height
    const canvasHeight = useMemo(() => {
        if (finalNodes.length === 0) return 400;
        const maxY = Math.max(...finalNodes.map((n) => n.y + n.radius));
        return Math.max(400, maxY + 60);
    }, [finalNodes]);

    // Focus dimming: which nodes/edges are "related" to the highlighted node
    const relatedNodeIds = useMemo(() => {
        if (!highlightedNodeId) return null;
        const related = new Set<string>([highlightedNodeId]);
        for (const edge of edges) {
            if (edge.source === highlightedNodeId) related.add(edge.target);
            if (edge.target === highlightedNodeId) related.add(edge.source);
        }
        return related;
    }, [highlightedNodeId, edges]);

    const isNodeDimmed = useCallback(
        (nodeId: string) => {
            if (!relatedNodeIds) return false;
            return !relatedNodeIds.has(nodeId);
        },
        [relatedNodeIds],
    );

    const isEdgeDimmed = useCallback(
        (edge: GraphEdge) => {
            if (hoveredEdgeType && edge.label !== hoveredEdgeType) return true;
            if (!relatedNodeIds) return false;
            return !relatedNodeIds.has(edge.source) || !relatedNodeIds.has(edge.target);
        },
        [relatedNodeIds, hoveredEdgeType],
    );

    // Drag handlers
    const handleMouseDown = useCallback(
        (e: React.MouseEvent, nodeId: string) => {
            e.preventDefault();
            const svg = svgRef.current;
            if (!svg) return;
            const pt = svg.createSVGPoint();
            pt.x = e.clientX;
            pt.y = e.clientY;
            const svgP = pt.matrixTransform(svg.getScreenCTM()?.inverse());
            const node = nodeMap[nodeId];
            if (!node) return;
            setDragState({
                nodeId,
                offsetX: svgP.x - node.x,
                offsetY: svgP.y - node.y,
            });
        },
        [nodeMap],
    );

    const handleMouseMove = useCallback(
        (e: React.MouseEvent) => {
            if (!dragState) return;
            const svg = svgRef.current;
            if (!svg) return;
            const pt = svg.createSVGPoint();
            pt.x = e.clientX;
            pt.y = e.clientY;
            const svgP = pt.matrixTransform(svg.getScreenCTM()?.inverse());
            setDragPositions((prev) => ({
                ...prev,
                [dragState.nodeId]: {
                    x: svgP.x - dragState.offsetX,
                    y: svgP.y - dragState.offsetY,
                },
            }));
        },
        [dragState],
    );

    const handleMouseUp = useCallback(() => {
        setDragState(null);
    }, []);

    if (nodes.length === 0) {
        return (
            <div className="flex h-full items-center justify-center text-muted-foreground">
                <p className="text-sm">No graph data available</p>
            </div>
        );
    }

    return (
        <div className="relative h-full w-full overflow-auto" style={{ background: "#f8fafc" }}>
            <svg
                ref={svgRef}
                viewBox={`0 0 ${CANVAS_WIDTH} ${canvasHeight}`}
                className="h-full w-full"
                style={{ minWidth: CANVAS_WIDTH, minHeight: canvasHeight }}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
            >
                <defs>
                    {/* Arrowhead markers for each edge type */}
                    {Object.entries(EDGE_STYLES).map(([type, style]) => (
                        <marker
                            key={type}
                            id={`arrow-${type}`}
                            viewBox="0 0 10 6"
                            refX="10"
                            refY="3"
                            markerWidth="8"
                            markerHeight="6"
                            orient="auto"
                        >
                            <path d="M 0 0 L 10 3 L 0 6 Z" fill={style.color} />
                        </marker>
                    ))}
                    <marker id="arrow-default" viewBox="0 0 10 6" refX="10" refY="3" markerWidth="8" markerHeight="6" orient="auto">
                        <path d="M 0 0 L 10 3 L 0 6 Z" fill="#94a3b8" />
                    </marker>
                </defs>

                {/* Column lane backgrounds */}
                {COLUMN_LABELS.map(({ x }, i) => {
                    const laneX = i === 0 ? 0 : (COLUMN_LABELS[i - 1].x + x) / 2;
                    const laneW = i === COLUMN_LABELS.length - 1 ? CANVAS_WIDTH - laneX : (x + (COLUMN_LABELS[i + 1]?.x ?? CANVAS_WIDTH)) / 2 - laneX;
                    return (
                        <rect
                            key={`lane-${i}`}
                            x={laneX}
                            y={0}
                            width={laneW}
                            height={canvasHeight}
                            fill={i % 2 === 0 ? "rgba(241,245,249,0.6)" : "rgba(248,250,252,0.3)"}
                        />
                    );
                })}

                {/* Column divider lines */}
                {COLUMN_LABELS.slice(1).map(({ x }, i) => {
                    const divX = (COLUMN_LABELS[i].x + x) / 2;
                    return (
                        <line
                            key={`div-${i}`}
                            x1={divX}
                            y1={30}
                            x2={divX}
                            y2={canvasHeight}
                            stroke="#e2e8f0"
                            strokeDasharray="4,4"
                            strokeWidth={1}
                        />
                    );
                })}

                {/* Column headers */}
                {COLUMN_LABELS.map(({ x, label }) => (
                    <text key={label} x={x} y={22} textAnchor="middle" fill="#64748b" fontSize={11} fontWeight={600} letterSpacing="0.05em">
                        {label}
                    </text>
                ))}

                {/* Flow arrows between column headers */}
                {COLUMN_LABELS.slice(0, -1).map(({ x }, i) => {
                    const nextX = COLUMN_LABELS[i + 1].x;
                    const midX = (x + nextX) / 2;
                    return (
                        <text key={`arrow-${i}`} x={midX} y={22} textAnchor="middle" fill="#cbd5e1" fontSize={14}>
                            {"\u2192"}
                        </text>
                    );
                })}

                {/* Edges */}
                {edges.map((edge) => {
                    const src = nodeMap[edge.source];
                    const tgt = nodeMap[edge.target];
                    if (!src || !tgt) return null;

                    const style = EDGE_STYLES[edge.label] || DEFAULT_EDGE_STYLE;
                    const dimmed = isEdgeDimmed(edge);
                    const markerId = EDGE_STYLES[edge.label] ? `arrow-${edge.label}` : "arrow-default";

                    return (
                        <path
                            key={edge.id}
                            d={edgePath(src.x, src.y, tgt.x, tgt.y)}
                            fill="none"
                            stroke={style.color}
                            strokeWidth={style.width}
                            strokeDasharray={style.dash || undefined}
                            markerEnd={`url(#${markerId})`}
                            opacity={dimmed ? 0.15 : 0.8}
                            className="transition-opacity duration-200"
                        />
                    );
                })}

                {/* Nodes */}
                {finalNodes.map((node) => {
                    const primaryLabel = node.labels[0] || "";
                    const style = NODE_STYLES[primaryLabel] || DEFAULT_NODE_STYLE;
                    const dimmed = isNodeDimmed(node.id);
                    const isHovered = hoveredNodeId === node.id;
                    const isHighlighted = highlightedNodeId === node.id;
                    const isPartial = primaryLabel === "Study" && node.properties.partial === true;

                    return (
                        <g
                            key={node.id}
                            data-testid={`graph-node-${node.id}`}
                            transform={`translate(${node.x}, ${node.y})`}
                            opacity={dimmed ? 0.25 : 1}
                            className="cursor-pointer transition-opacity duration-200"
                            onMouseEnter={() => setHoveredNodeId(node.id)}
                            onMouseLeave={() => setHoveredNodeId(null)}
                            onMouseDown={(e) => handleMouseDown(e, node.id)}
                            onClick={() => onNodeClick?.(node)}
                        >
                            {/* Hover glow */}
                            {isHovered && (
                                <circle r={node.radius + 6} fill="none" stroke={style.stroke} strokeWidth={2} opacity={0.3} />
                            )}

                            {/* Highlight ring */}
                            {isHighlighted && (
                                <circle r={node.radius + 4} fill="none" stroke={style.stroke} strokeWidth={2.5} opacity={0.6} />
                            )}

                            {/* Node circle */}
                            <circle
                                r={node.radius}
                                fill={isPartial ? "transparent" : style.fill}
                                stroke={style.stroke}
                                strokeWidth={isPartial ? 1.5 : 2}
                                strokeDasharray={isPartial ? "4,3" : undefined}
                            />

                            {/* Label */}
                            <text
                                y={node.radius + 14}
                                textAnchor="middle"
                                fill={style.text}
                                fontSize={10}
                                fontWeight={500}
                                className="pointer-events-none select-none"
                            >
                                {node.displayLabel}
                            </text>

                            {/* Degree badge for entities with degree > 3 */}
                            {node.degree > 3 && (
                                <g
                                    data-testid={`degree-badge-${node.id}`}
                                    transform={`translate(${node.radius * 0.7}, ${-node.radius * 0.7})`}
                                >
                                    <circle r={8} fill="#475569" />
                                    <text y={3.5} textAnchor="middle" fill="#fff" fontSize={9} fontWeight={600}>
                                        {node.degree}
                                    </text>
                                </g>
                            )}
                        </g>
                    );
                })}
            </svg>

            {/* Edge type legend */}
            <div className="absolute bottom-4 left-4 flex gap-4 rounded-lg bg-white/80 px-3 py-2 shadow-sm backdrop-blur-sm">
                {Object.entries(EDGE_STYLES).map(([type, style]) => (
                    <div
                        key={type}
                        className="flex cursor-pointer items-center gap-1.5 text-xs"
                        onMouseEnter={() => setHoveredEdgeType(type)}
                        onMouseLeave={() => setHoveredEdgeType(null)}
                    >
                        <svg width="20" height="8">
                            <line
                                x1={0}
                                y1={4}
                                x2={20}
                                y2={4}
                                stroke={style.color}
                                strokeWidth={style.width}
                                strokeDasharray={style.dash || undefined}
                            />
                        </svg>
                        <span style={{ color: style.color }} className="font-medium">
                            {type as GraphEdgeType}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
};

export default GraphCanvas;
