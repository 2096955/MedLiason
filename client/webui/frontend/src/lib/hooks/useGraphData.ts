import { useState, useEffect, useCallback, useMemo } from "react";

import type { GraphNode, GraphEdge } from "@/lib/types";

interface CytoscapeElement {
    data: Record<string, unknown>;
    group?: "nodes" | "edges";
}

interface UseGraphDataParams {
    mode: "session" | "explore";
    sessionId?: string | null;
    entityTypes?: string[];
}

interface UseGraphDataReturn {
    elements: CytoscapeElement[];
    isLoading: boolean;
    error: string | null;
    isEmpty: boolean;
    refetch: () => void;
}

function nodesToCytoscapeElements(nodes: GraphNode[], edges: GraphEdge[]): CytoscapeElement[] {
    // Compute degree for each node from edge list
    const degreeMap: Record<string, number> = {};
    for (const edge of edges) {
        degreeMap[edge.source] = (degreeMap[edge.source] ?? 0) + 1;
        degreeMap[edge.target] = (degreeMap[edge.target] ?? 0) + 1;
    }

    return nodes.map((node) => ({
        group: "nodes" as const,
        data: {
            ...node.properties,
            id: node.id,
            label: node.name,
            nodeLabel: node.labels?.[0] || "Unknown",
            description: node.description,
            degree: degreeMap[node.id] ?? 0,
        },
    }));
}

function edgesToCytoscapeElements(edges: GraphEdge[]): CytoscapeElement[] {
    return edges.map((edge) => ({
        group: "edges" as const,
        data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: edge.label,
        },
    }));
}

export function useGraphData({ mode, sessionId, entityTypes }: UseGraphDataParams): UseGraphDataReturn {
    const [nodes, setNodes] = useState<GraphNode[]>([]);
    const [edges, setEdges] = useState<GraphEdge[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [fetchKey, setFetchKey] = useState(0);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        setError(null);

        try {
            let url: string;

            if (mode === "session") {
                if (!sessionId) {
                    setNodes([]);
                    setEdges([]);
                    setIsLoading(false);
                    return;
                }
                url = `/api/v1/graph/session/${encodeURIComponent(sessionId)}`;
            } else {
                const params = new URLSearchParams({ limit: "100" });
                if (entityTypes && entityTypes.length > 0) {
                    params.set("labels", entityTypes.join(","));
                }
                url = `/api/v1/graph/explore?${params.toString()}`;
            }

            const res = await fetch(url, { credentials: "include" });

            if (!res.ok) {
                if (res.status === 404) {
                    setNodes([]);
                    setEdges([]);
                    setIsLoading(false);
                    return;
                }
                throw new Error(`Failed to fetch graph data (${res.status})`);
            }

            const data = await res.json();
            setNodes(data.nodes || []);
            setEdges(data.edges || []);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to fetch graph data");
            setNodes([]);
            setEdges([]);
        } finally {
            setIsLoading(false);
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetchKey is an intentional refetch trigger
    }, [mode, sessionId, entityTypes, fetchKey]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const elements = useMemo(() => {
        return [...nodesToCytoscapeElements(nodes, edges), ...edgesToCytoscapeElements(edges)];
    }, [nodes, edges]);

    const isEmpty = nodes.length === 0 && edges.length === 0;

    const refetch = useCallback(() => {
        setFetchKey((prev) => prev + 1);
    }, []);

    return { elements, isLoading, error, isEmpty, refetch };
}
