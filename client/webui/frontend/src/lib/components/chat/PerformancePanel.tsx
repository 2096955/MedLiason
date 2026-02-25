import React, { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, CircleAlert, Gauge, ChevronDown, ChevronRight, Users, TrendingUp, AlertTriangle, ArrowRight } from "lucide-react";
import { Button, Badge, Spinner } from "@/lib/components/ui";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AgentPerformance {
    name: string;
    active_version: number | null;
    composite_score: number | null;
    is_baseline: boolean;
    grader_scores?: {
        entity?: number | null;
        fidelity?: number | null;
        citation?: number | null;
        quality?: number | null;
        llm_judge?: number | null;
    };
}

interface Overview {
    total_agents: number;
    evolved_count: number;
    avg_composite: number | null;
    promotions_last_24h: number;
    agents_below_threshold: number;
}

interface Promotion {
    agent_name: string;
    from_version: number;
    to_version: number;
    reason: string;
    composite_before: number | null;
    composite_after: number | null;
    promoted_at: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return a Tailwind colour class based on composite score thresholds. */
function scoreColor(score: number | null): string {
    if (score === null) return "bg-muted-foreground/30";
    if (score >= 0.65) return "bg-[hsl(var(--chart-2))]"; // green
    if (score >= 0.55) return "bg-[hsl(var(--chart-4))]"; // yellow
    return "bg-destructive"; // red
}

/** Format a score as a rounded percentage string, e.g. "72%". */
function pct(score: number | null): string {
    if (score === null) return "n/a";
    return `${Math.round(score * 100)}%`;
}

/** Relative time label, e.g. "2 hours ago". */
function relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60_000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}

const GRADER_LABELS: Record<string, string> = {
    entity: "Entity",
    fidelity: "Fidelity",
    citation: "Citation",
    quality: "Quality",
    llm_judge: "LLM Judge",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const AgentRow: React.FC<{ agent: AgentPerformance }> = ({ agent }) => {
    const [expanded, setExpanded] = useState(false);
    const hasGraderScores = agent.grader_scores && Object.values(agent.grader_scores).some(v => v !== null && v !== undefined);

    return (
        <div className="rounded border border-border">
            <button
                type="button"
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm hover:bg-muted/50"
                onClick={() => hasGraderScores && setExpanded(prev => !prev)}
                disabled={!hasGraderScores}
            >
                {hasGraderScores ? (
                    expanded ? <ChevronDown className="h-3 w-3 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 flex-shrink-0" />
                ) : (
                    <span className="w-3 flex-shrink-0" />
                )}

                <span className="flex-1 truncate font-medium">{agent.name}</span>

                {agent.is_baseline && (
                    <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                        Baseline
                    </Badge>
                )}

                {agent.active_version !== null && (
                    <Badge variant="default" className="text-[10px] px-1.5 py-0">
                        v{agent.active_version}
                    </Badge>
                )}

                {/* Score bar */}
                <div className="flex w-20 items-center gap-1.5">
                    <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                            className={`absolute left-0 top-0 h-full rounded-full transition-all ${scoreColor(agent.composite_score)}`}
                            style={{ width: agent.composite_score !== null ? `${Math.round(agent.composite_score * 100)}%` : "0%" }}
                        />
                    </div>
                    <span className="text-muted-foreground w-8 text-right text-[10px] tabular-nums">{pct(agent.composite_score)}</span>
                </div>
            </button>

            {expanded && agent.grader_scores && (
                <div className="border-t border-border bg-muted/30 px-3 py-2">
                    <div className="space-y-1">
                        {Object.entries(GRADER_LABELS).map(([key, label]) => {
                            const value = agent.grader_scores?.[key as keyof typeof agent.grader_scores] ?? null;
                            return (
                                <div key={key} className="flex items-center gap-2 text-xs">
                                    <span className="text-muted-foreground w-16">{label}</span>
                                    <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-muted">
                                        <div
                                            className={`absolute left-0 top-0 h-full rounded-full ${scoreColor(value)}`}
                                            style={{ width: value !== null ? `${Math.round(value * 100)}%` : "0%" }}
                                        />
                                    </div>
                                    <span className="text-muted-foreground w-8 text-right tabular-nums">{pct(value)}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface PerformancePanelProps {
    isActive?: boolean;
}

export const PerformancePanel: React.FC<PerformancePanelProps> = ({ isActive = false }) => {
    const [overview, setOverview] = useState<Overview | null>(null);
    const [agents, setAgents] = useState<AgentPerformance[]>([]);
    const [promotions, setPromotions] = useState<Promotion[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasFetchedRef = useRef(false);

    const fetchData = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        try {
            const [overviewRes, agentsRes, promotionsRes] = await Promise.all([
                fetch("/api/v1/evolution/overview"),
                fetch("/api/v1/evolution/agents"),
                fetch("/api/v1/evolution/promotions?limit=5"),
            ]);

            if (!overviewRes.ok || !agentsRes.ok) {
                throw new Error("Failed to fetch performance data");
            }

            const overviewData = await overviewRes.json();
            const agentsData = await agentsRes.json();
            const promotionsData = promotionsRes.ok ? await promotionsRes.json() : [];

            setOverview(overviewData);
            setAgents(Array.isArray(agentsData) ? agentsData : []);
            setPromotions(Array.isArray(promotionsData) ? promotionsData : []);
            hasFetchedRef.current = true;
        } catch {
            setOverview(null);
            setAgents([]);
            setPromotions([]);
            setError("Could not reach the evolution API. The prompt evolution service may not be running.");
        } finally {
            setIsLoading(false);
        }
    }, []);

    // Only fetch when the tab becomes active for the first time
    useEffect(() => {
        if (isActive && !hasFetchedRef.current) {
            fetchData();
        }
    }, [isActive, fetchData]);

    const hasData = overview !== null && agents.length > 0;

    return (
        <div className="flex h-full flex-col">
            {/* Header */}
            <div className="flex items-center justify-between border-b px-4 py-3">
                <div>
                    <h3 className="text-sm font-semibold">Agent Performance</h3>
                    <p className="text-muted-foreground text-xs">
                        {hasData
                            ? `${overview.evolved_count}/${overview.total_agents} agents evolved`
                            : "Self-evolving prompt metrics"}
                    </p>
                </div>
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={fetchData}
                    disabled={isLoading}
                    tooltip="Refresh"
                    aria-label="Refresh performance data"
                >
                    <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
                </Button>
            </div>

            {/* Error banner */}
            {error && (
                <div
                    className="mx-4 mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/50 p-3"
                    role="alert"
                >
                    <CircleAlert className="text-muted-foreground mt-0.5 h-4 w-4 flex-shrink-0" />
                    <div className="flex-1">
                        <p className="text-muted-foreground text-xs">{error}</p>
                        <button
                            type="button"
                            className="mt-1 text-xs font-medium underline underline-offset-2"
                            onClick={fetchData}
                        >
                            Retry
                        </button>
                    </div>
                </div>
            )}

            {/* Loading state */}
            {isLoading && !hasData && (
                <div className="flex flex-1 items-center justify-center">
                    <Spinner size="medium" />
                </div>
            )}

            {/* Empty state */}
            {!isLoading && !error && !hasData && hasFetchedRef.current && (
                <div className="flex flex-1 flex-col items-center justify-center p-4">
                    <Gauge className="text-muted-foreground mb-3 h-10 w-10" />
                    <p className="text-muted-foreground text-sm font-medium">No data yet</p>
                    <p className="text-muted-foreground mt-1 text-center text-xs">
                        Performance metrics will appear here after agents process queries and the evolution system collects scores.
                    </p>
                </div>
            )}

            {/* Content */}
            {hasData && (
                <div className="flex-1 overflow-y-auto px-4 py-3">
                    {/* Overview section */}
                    <div className="mb-4">
                        <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                            <TrendingUp className="h-3 w-3" /> Overview
                        </h4>
                        <div className="grid grid-cols-2 gap-2">
                            <div className="rounded-md border border-border bg-muted/30 p-2">
                                <div className="text-muted-foreground text-[10px] uppercase tracking-wider">Total Agents</div>
                                <div className="mt-0.5 flex items-center gap-1.5">
                                    <Users className="text-muted-foreground h-3.5 w-3.5" />
                                    <span className="text-sm font-semibold">{overview.total_agents}</span>
                                </div>
                            </div>
                            <div className="rounded-md border border-border bg-muted/30 p-2">
                                <div className="text-muted-foreground text-[10px] uppercase tracking-wider">Evolved</div>
                                <div className="mt-0.5 text-sm font-semibold">{overview.evolved_count}</div>
                            </div>
                            <div className="col-span-2 rounded-md border border-border bg-muted/30 p-2">
                                <div className="text-muted-foreground text-[10px] uppercase tracking-wider">Avg Composite Score</div>
                                <div className="mt-1 flex items-center gap-2">
                                    <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-muted">
                                        <div
                                            className={`absolute left-0 top-0 h-full rounded-full transition-all ${scoreColor(overview.avg_composite)}`}
                                            style={{ width: overview.avg_composite !== null ? `${Math.round(overview.avg_composite * 100)}%` : "0%" }}
                                        />
                                    </div>
                                    <span className="text-xs font-semibold tabular-nums">{pct(overview.avg_composite)}</span>
                                </div>
                            </div>
                            {overview.agents_below_threshold > 0 && (
                                <div className="col-span-2 flex items-center gap-2 rounded-md border border-border bg-muted/30 p-2">
                                    <AlertTriangle className="h-3.5 w-3.5 text-[hsl(var(--chart-4))]" />
                                    <span className="text-xs">
                                        <span className="font-semibold text-[hsl(var(--chart-4))]">{overview.agents_below_threshold}</span>
                                        <span className="text-muted-foreground"> agent{overview.agents_below_threshold !== 1 ? "s" : ""} below threshold</span>
                                    </span>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Agent list */}
                    <div className="mb-4">
                        <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                            <Gauge className="h-3 w-3" /> Agents
                        </h4>
                        <div className="space-y-1.5">
                            {agents.map(agent => (
                                <AgentRow key={agent.name} agent={agent} />
                            ))}
                        </div>
                    </div>

                    {/* Recent promotions */}
                    {promotions.length > 0 && (
                        <div>
                            <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                                <TrendingUp className="h-3 w-3" /> Recent Promotions
                            </h4>
                            <div className="space-y-1.5">
                                {promotions.map((promo, idx) => (
                                    <div key={`${promo.agent_name}-${promo.promoted_at}-${idx}`} className="rounded border border-border px-2 py-1.5 text-sm">
                                        <div className="flex items-center gap-2">
                                            <span className="flex-1 truncate font-medium">{promo.agent_name}</span>
                                            <span className="text-muted-foreground flex items-center gap-1 text-xs tabular-nums">
                                                v{promo.from_version} <ArrowRight className="h-2.5 w-2.5" /> v{promo.to_version}
                                            </span>
                                        </div>
                                        <div className="mt-1 flex items-center gap-2">
                                            <Badge
                                                variant="outline"
                                                className={`text-[10px] px-1.5 py-0 ${
                                                    promo.reason === "evolution"
                                                        ? "border-[hsl(var(--chart-1))] text-[hsl(var(--chart-1))]"
                                                        : promo.reason === "auto_rollback"
                                                          ? "border-[hsl(var(--chart-4))] text-[hsl(var(--chart-4))]"
                                                          : ""
                                                }`}
                                            >
                                                {promo.reason}
                                            </Badge>
                                            {promo.composite_before !== null && promo.composite_after !== null && (
                                                <span className="text-muted-foreground text-[10px] tabular-nums">
                                                    {pct(promo.composite_before)} &rarr; {pct(promo.composite_after)}
                                                </span>
                                            )}
                                            <span className="text-muted-foreground ml-auto text-[10px]">{relativeTime(promo.promoted_at)}</span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
