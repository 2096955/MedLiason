import React, { useState, useEffect, useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { Network, RefreshCw, Database, Loader2 } from "lucide-react";

import { Button } from "@/lib/components/ui";
import { cn } from "@/lib/utils";
import { useGraphData } from "@/lib/hooks/useGraphData";
import CytoscapeGraph from "./CytoscapeGraph";
import EntityDetailPanel from "./EntityDetailPanel";
import GraphQueryBar from "./GraphQueryBar";
import type { GraphStats } from "@/lib/types";

type TabMode = "session" | "explore";

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

    const [activeTab, setActiveTab] = useState<TabMode>(sessionIdFromUrl ? "session" : "explore");
    const [entityFilter, setEntityFilter] = useState<EntityFilter>("All");
    const [selectedNode, setSelectedNode] = useState<{ id: string; data: Record<string, unknown> } | null>(null);
    const [highlightedNodes, setHighlightedNodes] = useState<string[]>([]);
    const [stats, setStats] = useState<GraphStats | null>(null);
    const [statsLoading, setStatsLoading] = useState(false);

    // Compute entity types for explore mode
    const entityTypes = useMemo(() => {
        if (entityFilter === "All") return undefined;
        return [entityFilter];
    }, [entityFilter]);

    const { elements, isLoading, error, isEmpty, refetch } = useGraphData({
        mode: activeTab,
        sessionId: activeTab === "session" ? sessionIdFromUrl : undefined,
        entityTypes: activeTab === "explore" ? entityTypes : undefined,
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

    // Switch tab when URL session param changes
    useEffect(() => {
        if (sessionIdFromUrl) {
            setActiveTab("session");
        }
    }, [sessionIdFromUrl]);

    const handleNodeClick = useCallback((nodeId: string, nodeData: Record<string, unknown>) => {
        setSelectedNode({ id: nodeId, data: nodeData });
    }, []);

    const handleCloseDetail = useCallback(() => {
        setSelectedNode(null);
    }, []);

    const handleNLQResult = useCallback((nodeIds: string[]) => {
        setHighlightedNodes(nodeIds);
        // Clear highlights after 5 seconds
        setTimeout(() => setHighlightedNodes([]), 5000);
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

            {/* Tabs */}
            <div className="flex items-center gap-1 border-b border-border px-6">
                <button
                    onClick={() => setActiveTab("session")}
                    className={cn(
                        "border-b-2 px-4 py-2.5 text-sm font-medium transition-colors",
                        activeTab === "session"
                            ? "border-primary text-foreground"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                >
                    Session
                </button>
                <button
                    onClick={() => setActiveTab("explore")}
                    className={cn(
                        "border-b-2 px-4 py-2.5 text-sm font-medium transition-colors",
                        activeTab === "explore"
                            ? "border-primary text-foreground"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                >
                    Knowledge Base
                </button>

                {/* Entity type filters (explore tab only) */}
                {activeTab === "explore" && (
                    <div className="ml-4 flex items-center gap-1.5">
                        {ENTITY_TYPES.map((type) => (
                            <button
                                key={type}
                                onClick={() => setEntityFilter(type)}
                                className={cn(
                                    "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
                                    ENTITY_FILTER_COLORS[type],
                                    entityFilter === type && "ring-1 ring-ring"
                                )}
                            >
                                {type}
                            </button>
                        ))}
                    </div>
                )}
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
                                {activeTab === "session" ? (
                                    <>
                                        <p className="text-sm font-medium text-foreground">
                                            {sessionIdFromUrl ? "No graph data for this session" : "No session selected"}
                                        </p>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {sessionIdFromUrl
                                                ? "Graph data is generated at the end of the research pipeline."
                                                : 'Click "View in Graph" on a completed research message to view its session graph.'}
                                        </p>
                                    </>
                                ) : (
                                    <>
                                        <p className="text-sm font-medium text-foreground">No entities found</p>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            The knowledge base will grow as research sessions complete.
                                        </p>
                                    </>
                                )}
                            </div>
                        </div>
                    )}

                    {!isLoading && !error && !isEmpty && (
                        <CytoscapeGraph
                            elements={elements}
                            onNodeClick={handleNodeClick}
                            highlightedNodes={highlightedNodes}
                        />
                    )}
                </div>

                {/* Entity detail panel (right side) */}
                {selectedNode && (
                    <EntityDetailPanel nodeId={selectedNode.id} nodeData={selectedNode.data} onClose={handleCloseDetail} />
                )}
            </div>

            {/* NLQ query bar */}
            <GraphQueryBar onResultNodes={handleNLQResult} />
        </div>
    );
};

export default KnowledgeGraphPage;
