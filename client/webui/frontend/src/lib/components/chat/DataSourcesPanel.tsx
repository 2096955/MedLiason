import React, { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, CircleAlert, Database, Search } from "lucide-react";
import { Button } from "@/lib/components/ui";
import { api } from "@/lib/api";

interface DataSource {
    name: string;
    type: string;
    description: string;
    status: "online" | "offline" | "available";
    url?: string | null;
}

interface DataSourcesHealth {
    sources: DataSource[];
    summary: {
        totalSources: number;
        onlineSources: number;
        agentsOnline: number;
        agentsTotal: number;
    };
}

/** Known MCP data sources for static fallback display */
const KNOWN_MCP_SOURCES = [
    { name: "PubMed", description: "Biomedical literature search", port: 9001 },
    { name: "ClinicalTrials.gov", description: "Clinical trial registry", port: 9002 },
    { name: "OpenFDA", description: "Drug events, labels, recalls", port: 9003 },
    { name: "CDC", description: "Disease surveillance data", port: 9004 },
    { name: "Regulatory", description: "FDA device clearances", port: 9005 },
    { name: "SEER", description: "Cancer statistics", port: 9006 },
    { name: "Genomic", description: "ClinVar & dbSNP variants", port: 9007 },
    { name: "Environmental", description: "EPA air quality & EJScreen", port: 9008 },
    { name: "Census/SDOH", description: "Social determinants of health", port: 9009 },
    { name: "Provider Intel", description: "CMS open payments data", port: 9010 },
    { name: "NHS 111", description: "NHS clinical guidance (Firecrawl)", port: 9016 },
    { name: "BMA Library", description: "UK clinical guidelines (Firecrawl)", port: 9017 },
    { name: "OpenEvidence", description: "AI-synthesised clinical evidence", port: 9018 },
];

const KNOWN_SEARCH_TOOLS = [
    { name: "DuckDuckGo", description: "Web search (no API key required)" },
    { name: "Document Search", description: "Search uploaded files via RAG index" },
];

interface DataSourcesPanelProps {
    isActive?: boolean;
}

export const DataSourcesPanel: React.FC<DataSourcesPanelProps> = ({ isActive = false }) => {
    const [health, setHealth] = useState<DataSourcesHealth | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasFetchedRef = useRef(false);

    const fetchHealth = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        try {
            const data = await api.webui.get("/api/v1/dataSources/health");
            setHealth(data as DataSourcesHealth);
            hasFetchedRef.current = true;
        } catch {
            setHealth(null);
            setError("Showing known sources.");
        } finally {
            setIsLoading(false);
        }
    }, []);

    // Only fetch when the tab becomes active for the first time
    useEffect(() => {
        if (isActive && !hasFetchedRef.current) {
            fetchHealth();
        }
    }, [isActive, fetchHealth]);

    const mcpSources = health?.sources.filter(s => s.type === "mcp") ?? [];
    const searchSources = health?.sources.filter(s => s.type === "search") ?? [];
    const onlineCount = health?.summary.onlineSources ?? 0;
    const totalCount = health?.summary.totalSources ?? 0;

    const statusDot = (status: string) => {
        if (status === "online") return "bg-[hsl(var(--chart-2))]";
        if (status === "available") return "bg-[hsl(var(--chart-4))]";
        return "bg-destructive";
    };

    // When API is unavailable, show a static listing of known sources
    const showStaticFallback = error && !health;

    return (
        <div className="flex h-full flex-col">
            {/* Header */}
            <div className="flex items-center justify-between border-b px-4 py-3">
                <div>
                    <h3 className="text-sm font-semibold">Data Sources</h3>
                    <p className="text-muted-foreground text-xs">
                        {showStaticFallback
                            ? `${KNOWN_MCP_SOURCES.length} configured sources`
                            : `${onlineCount}/${totalCount} MCP servers online`}
                    </p>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={fetchHealth}
                    disabled={isLoading}
                    tooltip="Refresh"
                    aria-label="Refresh data sources"
                >
                    <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                </Button>
            </div>

            {/* Error / info banner */}
            {error && (
                <div
                    className="mx-4 mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/50 p-3"
                    role="alert"
                >
                    <CircleAlert className="text-muted-foreground mt-0.5 h-4 w-4 flex-shrink-0" />
                    <p className="text-muted-foreground text-xs">{error}</p>
                </div>
            )}

            {/* Warning if all online sources are down */}
            {health && onlineCount === 0 && (
                <div
                    className="mx-4 mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/50 p-3"
                    role="alert"
                >
                    <CircleAlert className="text-muted-foreground mt-0.5 h-4 w-4 flex-shrink-0" />
                    <div className="text-muted-foreground text-xs">
                        <p className="font-medium">No data sources connected</p>
                        <p className="mt-1">Responses rely on LLM training data only. Start MCP servers for evidence-based answers.</p>
                    </div>
                </div>
            )}

            {/* Sources list */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
                {/* MCP Servers */}
                <div className="mb-4">
                    <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                        <Database className="h-3 w-3" /> MCP Servers
                    </h4>
                    <div className="space-y-1.5">
                        {showStaticFallback
                            ? KNOWN_MCP_SOURCES.map(source => (
                                  <div key={source.name} className="flex items-start gap-2 rounded px-2 py-1.5 text-sm">
                                      <span className="bg-muted-foreground/40 mt-1.5 h-2 w-2 flex-shrink-0 rounded-full" />
                                      <div className="min-w-0 flex-1">
                                          <span className="block truncate font-medium">{source.name}</span>
                                          <span className="text-muted-foreground block text-xs">{source.description}</span>
                                      </div>
                                  </div>
                              ))
                            : mcpSources.map(source => (
                                  <div key={source.name} className="flex items-start gap-2 rounded px-2 py-1.5 text-sm">
                                      <span
                                          className={`mt-1.5 h-2 w-2 flex-shrink-0 rounded-full ${statusDot(source.status)}`}
                                          aria-label={source.status}
                                      />
                                      <div className="min-w-0 flex-1">
                                          <span className="block truncate font-medium">{source.name}</span>
                                          <span className="text-muted-foreground block text-xs">{source.status}</span>
                                      </div>
                                  </div>
                              ))}
                    </div>
                </div>

                {/* Search Tools */}
                <div>
                    <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                        <Search className="h-3 w-3" /> Search Tools
                    </h4>
                    <div className="space-y-1.5">
                        {showStaticFallback
                            ? KNOWN_SEARCH_TOOLS.map(source => (
                                  <div key={source.name} className="flex items-start gap-2 rounded px-2 py-1.5 text-sm">
                                      <span className="bg-muted-foreground/40 mt-1.5 h-2 w-2 flex-shrink-0 rounded-full" />
                                      <div className="min-w-0 flex-1">
                                          <span className="block truncate font-medium">{source.name}</span>
                                          <span className="text-muted-foreground block text-xs">{source.description}</span>
                                      </div>
                                  </div>
                              ))
                            : searchSources.map(source => (
                                  <div key={source.name} className="flex items-start gap-2 rounded px-2 py-1.5 text-sm">
                                      <span
                                          className={`mt-1.5 h-2 w-2 flex-shrink-0 rounded-full ${statusDot(source.status)}`}
                                          aria-label={source.status}
                                      />
                                      <div className="min-w-0 flex-1">
                                          <span className="block truncate font-medium">{source.name}</span>
                                          <span className="text-muted-foreground block text-xs">{source.description}</span>
                                      </div>
                                  </div>
                              ))}
                    </div>
                </div>

                {/* Agents */}
                {health && (
                    <div className="mt-4">
                        <h4 className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">Agents</h4>
                        <div className="flex items-center gap-2 rounded px-2 py-1.5 text-sm">
                            <span
                                className={`h-2 w-2 flex-shrink-0 rounded-full ${
                                    health.summary.agentsOnline > 0 ? "bg-[hsl(var(--chart-2))]" : "bg-destructive"
                                }`}
                                aria-label={health.summary.agentsOnline > 0 ? "online" : "offline"}
                            />
                            <span className="font-medium">
                                {health.summary.agentsOnline}/{health.summary.agentsTotal} agents online
                            </span>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
