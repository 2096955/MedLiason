import { useState, useEffect, useCallback, useMemo } from "react";

import type { GraphNode, GraphEdge } from "@/lib/types";

interface UseGraphDataParams {
    sessionId?: string | null;
    /** Single entity type filter string (e.g. "Disease"), or undefined/null for all. */
    entityFilter?: string | null;
}

interface UseGraphDataReturn {
    nodes: GraphNode[];
    edges: GraphEdge[];
    isLoading: boolean;
    error: string | null;
    isEmpty: boolean;
    refetch: () => void;
}

export function useGraphData({ sessionId, entityFilter }: UseGraphDataParams): UseGraphDataReturn {
    const [nodes, setNodes] = useState<GraphNode[]>([]);
    const [edges, setEdges] = useState<GraphEdge[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [fetchKey, setFetchKey] = useState(0);

    // Stable labels string derived from entityFilter (primitive, no referential issues)
    const labelsParam = useMemo(() => {
        if (!entityFilter || entityFilter === "All") return "";
        return entityFilter;
    }, [entityFilter]);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        setError(null);

        try {
            let url: string;

            if (sessionId) {
                url = `/api/v1/graph/session/${encodeURIComponent(sessionId)}`;
            } else {
                const params = new URLSearchParams({ limit: "100" });
                if (labelsParam) {
                    params.set("labels", labelsParam);
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
    }, [sessionId, labelsParam, fetchKey]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const isEmpty = nodes.length === 0 && edges.length === 0;
    const refetch = useCallback(() => setFetchKey((prev) => prev + 1), []);

    return { nodes, edges, isLoading, error, isEmpty, refetch };
}
