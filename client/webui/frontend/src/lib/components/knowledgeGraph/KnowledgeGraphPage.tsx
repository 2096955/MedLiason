import React, { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { Network, RefreshCw, Database, Loader2 } from "lucide-react";

import { Button } from "@/lib/components/ui";
import { cn } from "@/lib/utils";
import { useChatContext } from "@/lib/hooks";
import { useGraphData } from "@/lib/hooks/useGraphData";
import GraphCanvas from "./GraphCanvas";
import EntityDetailPanel from "./EntityDetailPanel";
import GraphQueryBar from "./GraphQueryBar";
import type { GraphNode, GraphStats } from "@/lib/types";

const ENTITY_TYPES = ["Disease", "Drug", "Gene", "Study", "All"] as const;
type EntityFilter = (typeof ENTITY_TYPES)[number];

const ENTITY_FILTER_COLORS: Record<EntityFilter, string> = {
    Disease: "bg-red-500/20 text-red-400 border-red-500/30 hover:bg-red-500/30",
    Drug: "bg-blue-500/20 text-blue-400 border-blue-500/30 hover:bg-blue-500/30",
    Gene: "bg-green-500/20 text-green-400 border-green-500/30 hover:bg-green-500/30",
    Study: "bg-amber-500/20 text-amber-400 border-amber-500/30 hover:bg-amber-500/30",
    All: "bg-gray-500/20 text-gray-400 border-gray-500/30 hover:bg-gray-500/30",
};

const KnowledgeGraphPage: React.FC = () => {
    const [searchParams] = useSearchParams();
    const sessionIdFromUrl = searchParams.get("session");

    const [entityFilter, setEntityFilter] = useState<EntityFilter>("All");
    const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
    const [highlightedNodeId, setHighlightedNodeId] = useState<string | null>(null);
    const [stats, setStats] = useState<GraphStats | null>(null);
    const [statsLoading, setStatsLoading] = useState(false);
    const nlqTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const { ragData } = useChatContext();

    const { nodes, edges, isLoading, error, isEmpty, refetch } = useGraphData({
        sessionId: sessionIdFromUrl || undefined,
        entityFilter,
    });

    // Fetch stats
    const fetchStats = useCallback(async () => {
        setStatsLoading(true);
        try {
            const res = await fetch("/api/v1/graph/stats", { credentials: "include" });
            if (res.ok) {
                setStats(await res.json());
            }
        } catch {
            // Stats are non-critical, silently ignore
        } finally {
            setStatsLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchStats();
    }, [fetchStats]);

    // Pre-select session node when ?session=X is in URL
    useEffect(() => {
        if (sessionIdFromUrl) {
            setHighlightedNodeId(
                nodes.find((n) => n.labels.includes("Session") && n.properties.session_id === sessionIdFromUrl)?.id ?? null,
            );
        }
    }, [sessionIdFromUrl, nodes]);

    const handleNodeClick = useCallback((node: GraphNode) => {
        // Clear any pending NLQ highlight timeout so it doesn't override manual selection
        if (nlqTimeoutRef.current) {
            clearTimeout(nlqTimeoutRef.current);
            nlqTimeoutRef.current = null;
        }
        setSelectedNode(node);
        setHighlightedNodeId(node.id);
    }, []);

    const handleCloseDetail = useCallback(() => {
        setSelectedNode(null);
        setHighlightedNodeId(null);
    }, []);

    // MVP: always opens external URL. ragData is wired here for future cross-page
    // bridge (e.g., navigate to /chat and highlight source in the Sources panel).
    const handleViewSource = useCallback(({ type, value }: { type: string; value: string }) => {
        // Check if source exists in current session ragData (informational for now)
        const _matchInRag = ragData
            ?.flatMap(r => r.sources)
            .find(s => s.sourceUrl?.includes(value));
        const url = type === "pmid" ? `https://pubmed.ncbi.nlm.nih.gov/${value}/`
            : type === "nct_id" ? `https://clinicaltrials.gov/study/${value}`
            : `https://doi.org/${value}`;
        window.open(url, "_blank");
    }, [ragData]);

    const handleSearchSources = useCallback((entityName: string) => {
        // MVP: open PubMed search for the entity
        const encoded = encodeURIComponent(entityName);
        window.open(`https://pubmed.ncbi.nlm.nih.gov/?term=${encoded}`, "_blank");
    }, []);

    const handleNLQResult = useCallback((nodeIds: string[]) => {
        if (nlqTimeoutRef.current) {
            clearTimeout(nlqTimeoutRef.current);
        }
        if (nodeIds.length > 0) {
            setHighlightedNodeId(nodeIds[0]);
        }
        nlqTimeoutRef.current = setTimeout(() => {
            setHighlightedNodeId(null);
            nlqTimeoutRef.current = null;
        }, 5000);
    }, []);

    return (
        <div className="flex h-full flex-col">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
                <div className="flex items-center gap-3">
                    <Network className="h-5 w-5 text-muted-foreground" />
                    <h1 className="text-lg font-semibold text-foreground">Knowledge Graph</h1>
                    {stats && !statsLoading && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Database className="h-3.5 w-3.5" />
                            <span>{stats.total_nodes} nodes</span>
                            <span className="text-border">|</span>
                            <span>{stats.total_edges} edges</span>
                        </div>
                    )}
                    {statsLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
                </div>
                <Button variant="ghost" size="sm" onClick={() => { refetch(); fetchStats(); }} className="gap-1.5">
                    <RefreshCw className="h-3.5 w-3.5" />
                    Refresh
                </Button>
            </div>

            {/* Entity type filter bar */}
            <div className="flex items-center gap-1.5 border-b border-border px-6 py-2">
                {sessionIdFromUrl && (
                    <span className="mr-2 text-xs text-muted-foreground">
                        Session: <span className="font-mono text-foreground">{sessionIdFromUrl.slice(0, 12)}...</span>
                    </span>
                )}
                {ENTITY_TYPES.map((type) => (
                    <button
                        key={type}
                        onClick={() => setEntityFilter(type)}
                        className={cn(
                            "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                            ENTITY_FILTER_COLORS[type],
                            entityFilter === type && "ring-1 ring-ring",
                        )}
                    >
                        {type}
                    </button>
                ))}
            </div>

            {/* Main content area */}
            <div className="flex flex-1 overflow-hidden">
                {/* Graph canvas */}
                <div className="relative flex-1">
                    {isLoading && (
                        <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/80">
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader2 className="h-5 w-5 animate-spin" />
                                Loading graph...
                            </div>
                        </div>
                    )}

                    {error && (
                        <div className="flex h-full items-center justify-center">
                            <div className="text-center">
                                <p className="text-sm text-destructive">{error}</p>
                                <Button variant="ghost" size="sm" onClick={refetch} className="mt-2">
                                    Try again
                                </Button>
                            </div>
                        </div>
                    )}

                    {!isLoading && !error && isEmpty && (
                        <div className="flex h-full items-center justify-center">
                            <div className="text-center">
                                <Network className="mx-auto mb-3 h-12 w-12 text-muted-foreground/50" />
                                <p className="text-sm font-medium text-foreground">No graph data available</p>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    The knowledge graph grows as research sessions complete.
                                </p>
                            </div>
                        </div>
                    )}

                    {!isLoading && !error && !isEmpty && (
                        <GraphCanvas
                            nodes={nodes}
                            edges={edges}
                            onNodeClick={handleNodeClick}
                            highlightedNodeId={highlightedNodeId}
                        />
                    )}
                </div>

                {/* Entity detail panel (right side) */}
                {selectedNode && (
                    <EntityDetailPanel node={selectedNode} onClose={handleCloseDetail} onViewSource={handleViewSource} onSearchSources={handleSearchSources} />
                )}
            </div>

            {/* NLQ query bar */}
            <GraphQueryBar onResultNodes={handleNLQResult} />
        </div>
    );
};

export default KnowledgeGraphPage;
