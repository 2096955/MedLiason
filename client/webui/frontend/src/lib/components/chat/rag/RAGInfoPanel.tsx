import React, { useEffect, useMemo, useRef, useState } from "react";
import { FileText, TrendingUp, Search, Link2, ChevronDown, ChevronUp, Brain, Globe, ExternalLink, AlertTriangle, Network } from "lucide-react";
// Web-only version - enterprise icons removed
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/lib/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/lib/components/ui/select";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { RAGSearchResult, RAGSource, PipelineErrorData } from "@/lib/types";

interface TimelineEvent {
    type: "thinking" | "search" | "read";
    timestamp: string;
    content: string;
    url?: string;
    favicon?: string;
    title?: string;
    source_type?: string;
}

interface RAGInfoPanelProps {
    ragData: RAGSearchResult[] | null;
    enabled: boolean;
    highlightedSourceId?: string | null;
    onHighlightConsumed?: () => void;
    pipelineErrors?: PipelineErrorData | null;
}

type SortMode = "grade" | "recent" | "relevance";

const GRADE_ORDER: Record<string, number> = {
    High: 4,
    Moderate: 3,
    Low: 2,
    "Very Low": 1,
};

function sortSources(sources: RAGSource[], mode: SortMode): RAGSource[] {
    return [...sources].sort((a, b) => {
        if (mode === "grade") {
            const gradeA = GRADE_ORDER[a.evidenceGrade || a.metadata?.evidence_grade || ""] || 0;
            const gradeB = GRADE_ORDER[b.evidenceGrade || b.metadata?.evidence_grade || ""] || 0;
            if (gradeB !== gradeA) return gradeB - gradeA;
            // Tie-break by year (newest first)
            const yearA = a.publicationYear || a.metadata?.publication_year || 0;
            const yearB = b.publicationYear || b.metadata?.publication_year || 0;
            return yearB - yearA;
        }
        if (mode === "recent") {
            const yearA = a.publicationYear || a.metadata?.publication_year || 0;
            const yearB = b.publicationYear || b.metadata?.publication_year || 0;
            if (yearB !== yearA) return yearB - yearA;
            // Tie-break by grade
            const gradeA = GRADE_ORDER[a.evidenceGrade || a.metadata?.evidence_grade || ""] || 0;
            const gradeB = GRADE_ORDER[b.evidenceGrade || b.metadata?.evidence_grade || ""] || 0;
            return gradeB - gradeA;
        }
        // relevance — sort by relevanceScore descending
        return (b.relevanceScore || 0) - (a.relevanceScore || 0);
    });
}

/**
 * Extract clean filename from file_id by removing session prefix
 * Example: "sam_dev_user_web-session-xxx_filename.pdf_v0.pdf" -> "filename.pdf"
 */
const extractFilename = (filename: string | undefined): string => {
    if (!filename) return "Unknown";

    // The pattern is: sam_dev_user_web-session-{uuid}_{actual_filename}_v{version}.pdf
    // We need to extract just the {actual_filename}.pdf part

    // First, remove the .pdf extension at the very end (added by backend)
    let cleaned = filename.replace(/\.pdf$/, "");

    // Remove the version suffix (_v0, _v1, etc.)
    cleaned = cleaned.replace(/_v\d+$/, "");

    // Now we have: sam_dev_user_web-session-{uuid}_{actual_filename}
    // Find the pattern "web-session-{uuid}_" and remove everything before and including it
    const sessionPattern = /^.*web-session-[a-f0-9-]+_/;
    cleaned = cleaned.replace(sessionPattern, "");

    // Add back the .pdf extension
    return cleaned + ".pdf";
};


// --- Evidence grade badge helpers ---

const GRADE_BADGE_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
    High: { bg: "bg-green-500/15", text: "text-green-700 dark:text-green-400", dot: "bg-green-500" },
    Moderate: { bg: "bg-amber-500/15", text: "text-amber-700 dark:text-amber-400", dot: "bg-amber-500" },
    Low: { bg: "bg-orange-500/15", text: "text-orange-700 dark:text-orange-400", dot: "bg-orange-500" },
    "Very Low": { bg: "bg-red-500/15", text: "text-red-700 dark:text-red-400", dot: "bg-red-500" },
};

function getSourceGrade(source: RAGSource): string | undefined {
    return source.evidenceGrade || source.metadata?.evidence_grade || undefined;
}

function getSourceYear(source: RAGSource): number | undefined {
    return source.publicationYear || source.metadata?.publication_year || undefined;
}

/** Color-coded pill badge showing evidence grade (High / Moderate / Low / Very Low). */
const GradeBadge: React.FC<{ grade: string }> = ({ grade }) => {
    const colors = GRADE_BADGE_COLORS[grade];
    if (!colors) return null;
    return (
        <span className={`inline-flex flex-shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium leading-none ${colors.bg} ${colors.text}`}>
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${colors.dot}`} />
            {grade}
        </span>
    );
};

const SourceCard: React.FC<{
    source: RAGSearchResult["sources"][0];
    isHighlighted?: boolean;
}> = ({ source, isHighlighted }) => {
    const [isExpanded, setIsExpanded] = React.useState(false);
    const contentPreview = source.contentPreview;
    const sourceType = source.sourceType || "web";

    // For image sources, use the source page link (not the imageUrl)
    let sourceUrl: string;
    let displayTitle: string;

    if (sourceType === "image") {
        sourceUrl = source.sourceUrl || source.metadata?.link || "";
        displayTitle = source.metadata?.title || source.filename || "Image source";
    } else {
        sourceUrl = source.sourceUrl || source.url || "";
        displayTitle = source.title || source.filename || extractFilename(source.fileId);
    }

    // Don't show content preview if it's just "Reading..." placeholder
    const hasRealContent = contentPreview && contentPreview !== "Reading...";
    const shouldTruncate = hasRealContent && contentPreview.length > 200;
    const displayContent = shouldTruncate && !isExpanded ? contentPreview.substring(0, 200) + "..." : contentPreview;

    const grade = getSourceGrade(source);
    const year = getSourceYear(source);

    // Only show score if it's a real relevance score (not the default 1.0 from deep research)
    const showScore = source.relevanceScore !== 1.0;

    return (
        <div data-citation-id={source.citationId} className={`bg-muted/50 border-border/50 flex flex-col rounded border p-3 ${isHighlighted ? "highlight-pulse" : ""}`}>
            {/* Source Header */}
            <div className="mb-2 flex flex-shrink-0 items-center justify-between">
                <div className="flex min-w-0 flex-1 items-center gap-2">
                    <FileText className="text-muted-foreground h-3 w-3 flex-shrink-0" />
                    {sourceUrl ? (
                        <a
                            href={sourceUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 truncate text-xs font-medium text-[var(--color-primary-wMain)] hover:text-[var(--color-primary-w60)] hover:underline dark:text-[var(--color-primary-w60)] dark:hover:text-[var(--color-white)]"
                            title={displayTitle}
                        >
                            <span className="truncate">{displayTitle}</span>
                            <ExternalLink className="h-2.5 w-2.5 flex-shrink-0" />
                        </a>
                    ) : (
                        <span className="truncate text-xs font-medium" title={displayTitle}>
                            {displayTitle}
                        </span>
                    )}
                    {year && <span className="text-muted-foreground flex-shrink-0 text-[10px]">({year})</span>}
                    {grade && <GradeBadge grade={grade} />}
                </div>
                {showScore && (
                    <div className="ml-2 flex flex-shrink-0 items-center gap-1 text-xs font-medium">
                        <TrendingUp className="h-3 w-3" />
                        <span>Score: {source.relevanceScore.toFixed(2)}</span>
                    </div>
                )}
            </div>


            {/* Content Preview - Fixed height when collapsed - Only show if we have real content */}
            {hasRealContent && <div className={`text-muted-foreground overflow-hidden text-xs leading-relaxed break-words whitespace-pre-wrap ${isExpanded ? "" : "h-[72px]"}`}>{displayContent}</div>}

            {/* Expand/Collapse Button */}
            {shouldTruncate && (
                <button onClick={() => setIsExpanded(!isExpanded)} className="text-primary mt-2 flex flex-shrink-0 items-center gap-1 text-xs hover:underline">
                    {isExpanded ? (
                        <>
                            <ChevronUp className="h-3 w-3" />
                            Show less
                        </>
                    ) : (
                        <>
                            <ChevronDown className="h-3 w-3" />
                            Show more
                        </>
                    )}
                </button>
            )}

            {/* Metadata (if available) */}
            {source.metadata && Object.keys(source.metadata).length > 0 && (
                <div className="border-border/50 mt-2 flex-shrink-0 border-t pt-2">
                    <details className="text-xs">
                        <summary className="text-muted-foreground hover:text-foreground cursor-pointer">Metadata</summary>
                        <div className="mt-1 space-y-1 pl-2">
                            {Object.entries(source.metadata).map(([key, value]) => (
                                <div key={key} className="flex gap-2">
                                    <span className="font-medium">{key}:</span>
                                    <span className="text-muted-foreground">{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>
                                </div>
                            ))}
                        </div>
                    </details>
                </div>
            )}
        </div>
    );
};

/** Virtualized list for SourceCards — only virtualizes when items exceed VIRTUALIZE_THRESHOLD */
const VIRTUALIZE_THRESHOLD = 20;

const VirtualizedSourceCardList: React.FC<{
    ragData: RAGSearchResult[];
    highlightedSourceId?: string | null;
    sortMode: SortMode;
}> = ({ ragData, highlightedSourceId, sortMode }) => {
    const parentRef = useRef<HTMLDivElement>(null);

    // Flatten sources for virtualization
    const flatSources = React.useMemo(() => {
        const sources: RAGSearchResult["sources"][0][] = [];
        ragData.forEach(search => {
            search.sources.forEach(source => {
                const sourceType = source.sourceType || "web";
                if (sourceType === "image") {
                    if (source.sourceUrl || source.metadata?.link) sources.push(source);
                } else {
                    sources.push(source);
                }
            });
        });
        return sources;
    }, [ragData]);

    // Apply sort
    const sortedSources = useMemo(() => sortSources(flatSources, sortMode), [flatSources, sortMode]);

    const virtualizer = useVirtualizer({
        count: sortedSources.length,
        getScrollElement: () => parentRef.current,
        estimateSize: () => 120,
        overscan: 10,
    });

    // For small lists, skip virtualization
    if (sortedSources.length <= VIRTUALIZE_THRESHOLD) {
        return (
            <div className="space-y-2">
                {sortedSources.map((source, idx) => (
                    <SourceCard key={idx} source={source} isHighlighted={highlightedSourceId === source.citationId} />
                ))}
            </div>
        );
    }

    return (
        <div ref={parentRef} className="overflow-y-auto" style={{ height: "100%", maxHeight: "calc(100vh - 200px)" }}>
            <div style={{ height: `${virtualizer.getTotalSize()}px`, width: "100%", position: "relative" }}>
                {virtualizer.getVirtualItems().map(virtualRow => (
                    <div
                        key={virtualRow.key}
                        ref={virtualizer.measureElement}
                        data-index={virtualRow.index}
                        style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            transform: `translateY(${virtualRow.start}px)`,
                            paddingBottom: "8px",
                        }}
                    >
                        <SourceCard source={sortedSources[virtualRow.index]} isHighlighted={highlightedSourceId === sortedSources[virtualRow.index].citationId} />
                    </div>
                ))}
            </div>
        </div>
    );
};

export const RAGInfoPanel: React.FC<RAGInfoPanelProps> = ({ ragData, enabled, highlightedSourceId, onHighlightConsumed, pipelineErrors }) => {
    const [sortMode, setSortMode] = useState<SortMode>("grade");

    // Scroll to highlighted source when citation is clicked
    useEffect(() => {
        if (!highlightedSourceId) return;

        // Small delay to ensure DOM is updated after tab switch
        const timeout = setTimeout(() => {
            const el = document.querySelector(`[data-citation-id="${highlightedSourceId}"]`);
            if (el) {
                el.scrollIntoView({ behavior: "smooth", block: "center" });
                el.classList.add("highlight-pulse");
                setTimeout(() => {
                    el.classList.remove("highlight-pulse");
                    onHighlightConsumed?.();
                }, 2000);
            } else {
                onHighlightConsumed?.();
            }
        }, 150);
        return () => clearTimeout(timeout);
    }, [highlightedSourceId, onHighlightConsumed]);
    if (!enabled) {
        return (
            <div className="flex h-full items-center justify-center p-4">
                <div className="text-muted-foreground text-center">
                    <Link2 className="mx-auto mb-4 h-12 w-12 opacity-50" />
                    <div className="text-lg font-medium">RAG Sources</div>
                    <div className="mt-2 text-sm">RAG source visibility is disabled in settings</div>
                </div>
            </div>
        );
    }

    if (!ragData || ragData.length === 0) {
        return (
            <div className="flex h-full items-center justify-center p-4">
                <div className="text-muted-foreground text-center">
                    <Search className="mx-auto mb-4 h-12 w-12 opacity-50" />
                    <div className="text-lg font-medium">Sources</div>
                    <div className="mt-2 text-sm">No sources available yet</div>
                    <div className="mt-1 text-xs">Sources from web research will appear here after completion</div>
                </div>
            </div>
        );
    }

    const isAllDeepResearch = ragData.every(search => search.searchType === "deep_research" || search.searchType === "web_search" || search.searchType === "kb_search");

    // Calculate total sources across all searches (including images with valid source links)
    const totalSources = ragData.reduce((sum, search) => {
        const validSources = search.sources.filter(s => {
            const sourceType = s.sourceType || "web";
            // For images, only count if they have a source link (not just imageUrl)
            if (sourceType === "image") {
                return s.sourceUrl || s.metadata?.link;
            }
            return true;
        });
        return sum + validSources.length;
    }, 0);

    // Simple source item component for deep research
    const SimpleSourceItem: React.FC<{ source: RAGSearchResult["sources"][0]; isHighlighted?: boolean }> = ({ source, isHighlighted }) => {
        const grade = getSourceGrade(source);
        const year = getSourceYear(source);
        const sourceType = source.sourceType || "web";

        // For image sources, use the source page link (not the imageUrl)
        let url: string;
        let title: string;

        if (sourceType === "image") {
            url = source.sourceUrl || source.metadata?.link || "";
            title = source.metadata?.title || source.filename || "Image source";
        } else {
            url = source.url || source.sourceUrl || "";
            title = source.title || source.filename || "Unknown";
        }

        const favicon = source.metadata?.favicon || (url ? `https://www.google.com/s2/favicons?domain=${url}&sz=32` : "");

        return (
            <div data-citation-id={source.citationId} className={`hover:bg-muted/50 -mx-2 flex items-center gap-2 rounded px-2 py-1.5 ${isHighlighted ? "highlight-pulse" : ""}`}>
                {favicon && (
                    <img
                        src={favicon}
                        alt=""
                        className="h-4 w-4 flex-shrink-0 rounded"
                        onError={e => {
                            (e.target as HTMLImageElement).style.display = "none";
                        }}
                    />
                )}
                {url ? (
                    <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 truncate text-sm text-[var(--color-primary-wMain)] hover:text-[var(--color-primary-w60)] hover:underline dark:text-[var(--color-primary-w60)] dark:hover:text-[var(--color-white)]"
                        title={title}
                    >
                        <span className="truncate">{title}</span>
                        <ExternalLink className="h-3 w-3 flex-shrink-0" />
                    </a>
                ) : (
                    <span className="truncate text-sm" title={title}>
                        {title}
                    </span>
                )}
                {year && <span className="text-muted-foreground ml-0.5 flex-shrink-0 text-[10px]">({year})</span>}
                {grade && <GradeBadge grade={grade} />}
            </div>
        );
    };

    // Helper function to check if a source was fully fetched
    const isSourceFullyFetched = (source: RAGSearchResult["sources"][0]): boolean => {
        return source.metadata?.fetched === true || source.metadata?.fetch_status === "success" || (source.contentPreview ? source.contentPreview.includes("[Full Content Fetched]") : false);
    };

    // Get all unique sources grouped by fully read vs snippets (for deep research)
    const { fullyReadSources, snippetSources, allUniqueSources } = (() => {
        if (!isAllDeepResearch) return { fullyReadSources: [], snippetSources: [], allUniqueSources: [] };

        const fullyReadMap = new Map<string, RAGSearchResult["sources"][0]>();
        const snippetMap = new Map<string, RAGSearchResult["sources"][0]>();

        // Check if this is web_search (no fetched metadata) or deep_research (has fetched metadata)
        const isWebSearch = ragData.some(search => search.searchType === "web_search");
        const isDeepResearch = ragData.some(search => search.searchType === "deep_research");

        ragData.forEach(search => {
            search.sources.forEach(source => {
                const sourceType = source.sourceType || "web";

                // For image sources: include if they have a source link (not just imageUrl)
                if (sourceType === "image") {
                    const sourceLink = source.sourceUrl || source.metadata?.link;
                    if (!sourceLink) {
                        return; // Skip images without source links
                    }
                    // Images are always considered "fully read" if they have a source link
                    if (!fullyReadMap.has(sourceLink)) {
                        fullyReadMap.set(sourceLink, source);
                    }
                    return;
                }

                const key = source.url || source.sourceUrl || source.title || "";
                if (!key) return;

                // For web_search: all sources go to fully read (no distinction)
                if (isWebSearch && !isDeepResearch) {
                    if (!fullyReadMap.has(key)) {
                        fullyReadMap.set(key, source);
                    }
                    return;
                }

                // For deep_research: separate into fully read vs snippets
                const wasFetched = isSourceFullyFetched(source);
                if (wasFetched) {
                    if (!fullyReadMap.has(key)) {
                        fullyReadMap.set(key, source);
                    }
                    // Remove from snippets if it was previously added there
                    snippetMap.delete(key);
                } else {
                    // Only add to snippets if not already in fully read
                    if (!fullyReadMap.has(key) && !snippetMap.has(key)) {
                        snippetMap.set(key, source);
                    }
                }
            });
        });

        const fullyRead = Array.from(fullyReadMap.values());
        const snippets = Array.from(snippetMap.values());
        const all = [...fullyRead, ...snippets];

        console.log("[RAGInfoPanel] Source filtering:", {
            isWebSearch,
            isDeepResearch,
            totalSourcesBeforeFilter: ragData.reduce((sum, s) => sum + s.sources.length, 0),
            fullyReadSources: fullyRead.length,
            snippetSources: snippets.length,
            sampleFullyRead: fullyRead.slice(0, 3).map(s => ({
                url: s.url,
                title: s.title,
                fetched: s.metadata?.fetched,
                fetch_status: s.metadata?.fetch_status,
                contentPreview: s.contentPreview?.substring(0, 100),
                hasMarker: s.contentPreview?.includes("[Full Content Fetched]"),
            })),
            sampleSnippets: snippets.slice(0, 3).map(s => ({
                url: s.url,
                title: s.title,
                fetched: s.metadata?.fetched,
                fetch_status: s.metadata?.fetch_status,
                contentPreview: s.contentPreview?.substring(0, 100),
                hasMarker: s.contentPreview?.includes("[Full Content Fetched]"),
            })),
        });

        return { fullyReadSources: fullyRead, snippetSources: snippets, allUniqueSources: all };
    })();

    // Check if we should show grouped view (only for deep_research with both types)
    const isDeepResearch = ragData.some(search => search.searchType === "deep_research");
    const showGroupedSources = isDeepResearch && (fullyReadSources.length > 0 || snippetSources.length > 0);

    // Get the title from the first ragData entry (prefer LLM-generated title, fallback to query)
    const panelTitle = ragData && ragData.length > 0 ? ragData[0].title || ragData[0].query : "";

    // Memoized sorted arrays — avoids re-sorting on every render
    const sortedFullyRead = useMemo(() => sortSources(fullyReadSources, sortMode), [fullyReadSources, sortMode]);
    const sortedSnippets = useMemo(() => sortSources(snippetSources, sortMode), [snippetSources, sortMode]);
    const sortedAllUnique = useMemo(() => sortSources(allUniqueSources, sortMode), [allUniqueSources, sortMode]);

    const hasAnyFetchedSources = isDeepResearch && ragData.some(search => search.sources.some(s => s.metadata?.fetched === true || s.metadata?.fetch_status === "success"));

    return (
        <div className="flex h-full flex-col overflow-hidden">
            {isAllDeepResearch ? (
                // Deep research: Show sources grouped by fully read vs snippets (only when complete)
                <div className="flex flex-1 flex-col overflow-hidden">
                    <div className="flex items-center justify-between px-3 py-2 border-b">
                        <span className="text-xs text-muted-foreground font-medium">{allUniqueSources.length} sources</span>
                        <Select value={sortMode} onValueChange={val => setSortMode(val as SortMode)}>
                            <SelectTrigger className="h-7 w-[140px] text-xs">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="grade">Evidence Grade</SelectItem>
                                <SelectItem value="recent">Most Recent</SelectItem>
                                <SelectItem value="relevance">Relevance</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                        {/* Title section showing research question or query */}
                        {panelTitle && (
                            <div className="border-border/50 mb-4 border-b pb-3">
                                <h2 className="text-foreground text-base leading-tight font-semibold">{panelTitle}</h2>
                            </div>
                        )}

                        {/* Knowledge Graph sources — rendered before grouped research sources */}
                        {ragData?.some(r => r.searchType === "kb_search") && (
                            <div className="mb-3">
                                <div className="flex items-center gap-2 mb-2 text-sm font-medium text-purple-400">
                                    <Network className="h-4 w-4" />
                                    Knowledge Graph
                                </div>
                                <div className="space-y-2">
                                    {ragData
                                        .filter(r => r.searchType === "kb_search")
                                        .flatMap(r => r.sources)
                                        .map((source) => (
                                            <div key={source.citationId} className="border-l-2 border-purple-500/40 pl-2">
                                                <SourceCard source={source} isHighlighted={highlightedSourceId === source.citationId} />
                                            </div>
                                        ))}
                                </div>
                            </div>
                        )}

                        {/* Show grouped sources ONLY when research is complete (has fetched sources) */}
                        {showGroupedSources && hasAnyFetchedSources ? (
                            <>
                                {/* Fully Read Sources Section */}
                                {sortedFullyRead.length > 0 && (
                                    <div className="mb-4">
                                        <div className="mb-2">
                                            <h3 className="text-muted-foreground text-sm font-semibold">
                                                {sortedFullyRead.length} Fully Read Source{sortedFullyRead.length !== 1 ? "s" : ""}
                                            </h3>
                                        </div>
                                        <div className="space-y-1">
                                            {sortedFullyRead.map((source, idx) => (
                                                <SimpleSourceItem key={`fully-read-${idx}`} source={source} />
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Partially Read Sources Section */}
                                {sortedSnippets.length > 0 && (
                                    <div>
                                        <div className="mb-2">
                                            <h3 className="text-muted-foreground text-sm font-semibold">
                                                {sortedSnippets.length} Partially Read Source{sortedSnippets.length !== 1 ? "s" : ""}
                                            </h3>
                                            <p className="text-muted-foreground mt-0.5 text-xs">Search result snippets</p>
                                        </div>
                                        <div className="space-y-1">
                                            {sortedSnippets.map((source, idx) => (
                                                <SimpleSourceItem key={`partially-read-${idx}`} source={source} />
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : (
                            <>
                                <div className="mb-3">
                                    <h3 className="text-muted-foreground text-sm font-semibold">{isDeepResearch && !hasAnyFetchedSources ? "Sources Explored So Far" : `${sortedAllUnique.length} Sources`}</h3>
                                    {isDeepResearch && !hasAnyFetchedSources && <p className="text-muted-foreground mt-0.5 text-xs">Research in progress...</p>}
                                </div>
                                <div className="space-y-1">
                                    {sortedAllUnique.map((source, idx) => (
                                        <SimpleSourceItem key={`source-${idx}`} source={source} />
                                    ))}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            ) : (
                // Regular RAG/web search: Show both Activity and Sources tabs
                <Tabs defaultValue="activity" className="flex flex-1 flex-col overflow-hidden">
                    <div className="flex-shrink-0 px-4 pt-4 pb-2">
                        <TabsList className="grid w-full grid-cols-2">
                            <TabsTrigger value="activity">Activity</TabsTrigger>
                            <TabsTrigger value="sources">{totalSources} Sources</TabsTrigger>
                        </TabsList>
                    </div>

                    <TabsContent value="activity" className="mt-0 min-h-0 flex-1 overflow-y-auto px-4 pb-4">
                        <div className="mb-3">
                            <h3 className="text-muted-foreground text-sm font-semibold tracking-wide uppercase">Timeline of Research Activity</h3>
                            <p className="text-muted-foreground mt-1 text-xs">
                                {ragData.length} search{ragData.length !== 1 ? "es" : ""} performed
                            </p>
                        </div>

                        <div className="space-y-2">
                            {ragData.map((search, searchIdx) => {
                                // Build timeline events for this search
                                const events: TimelineEvent[] = [];

                                // Add search event
                                events.push({
                                    type: "search",
                                    timestamp: search.timestamp,
                                    content: search.query,
                                });

                                // Add read events for sources that were fetched/analyzed
                                search.sources.forEach(source => {
                                    if (source.url || source.title) {
                                        const sourceType = source.metadata?.source_type || "web";
                                        events.push({
                                            type: "read",
                                            timestamp: source.retrievedAt || search.timestamp,
                                            content: source.title || source.url || "Unknown",
                                            url: source.url,
                                            favicon: source.metadata?.favicon || (source.url ? `https://www.google.com/s2/favicons?domain=${source.url}&sz=32` : ""),
                                            title: source.title,
                                            source_type: sourceType,
                                        });
                                    }
                                });

                                return (
                                    <React.Fragment key={searchIdx}>
                                        {events.map((event, eventIdx) => (
                                            <div key={`${searchIdx}-${eventIdx}`} className="flex items-start gap-3 py-2">
                                                {/* Icon */}
                                                <div className="mt-0.5 flex-shrink-0">
                                                    {event.type === "thinking" && <Brain className="text-muted-foreground h-4 w-4" />}
                                                    {event.type === "search" && <Search className="text-muted-foreground h-4 w-4" />}
                                                    {event.type === "read" &&
                                                        (() => {
                                                            // Web-only version - only web sources
                                                            if (event.favicon && event.favicon.trim() !== "") {
                                                                // Web source with favicon
                                                                return (
                                                                    <img
                                                                        src={event.favicon}
                                                                        alt=""
                                                                        className="h-4 w-4 rounded"
                                                                        onError={e => {
                                                                            (e.target as HTMLImageElement).style.display = "none";
                                                                        }}
                                                                    />
                                                                );
                                                            } else {
                                                                // Web source without favicon or unknown
                                                                return <Globe className="text-muted-foreground h-4 w-4" />;
                                                            }
                                                        })()}
                                                </div>

                                                {/* Content */}
                                                <div className="min-w-0 flex-1">
                                                    {event.type === "search" && (
                                                        <div className="text-sm">
                                                            <span className="text-muted-foreground">Searched for </span>
                                                            <span className="font-medium">{event.content}</span>
                                                        </div>
                                                    )}
                                                    {event.type === "read" && (
                                                        <div className="text-sm">
                                                            <span className="text-muted-foreground">Read </span>
                                                            {event.url ? (
                                                                <a
                                                                    href={event.url}
                                                                    target="_blank"
                                                                    rel="noopener noreferrer"
                                                                    className="inline-flex items-center gap-1 font-medium text-[var(--color-primary-wMain)] hover:text-[var(--color-primary-w60)] hover:underline dark:text-[var(--color-primary-w60)] dark:hover:text-[var(--color-white)]"
                                                                >
                                                                    <span>{event.title || new URL(event.url).hostname}</span>
                                                                    <ExternalLink className="h-3 w-3 flex-shrink-0" />
                                                                </a>
                                                            ) : (
                                                                <span className="font-medium">{event.content}</span>
                                                            )}
                                                        </div>
                                                    )}
                                                    {event.type === "thinking" && <div className="text-muted-foreground text-sm">{event.content}</div>}
                                                </div>
                                            </div>
                                        ))}
                                    </React.Fragment>
                                );
                            })}
                        </div>
                    </TabsContent>

                    <TabsContent value="sources" className="mt-0 min-h-0 flex-1 overflow-hidden flex flex-col">
                        <div className="flex items-center justify-between px-3 py-2 border-b">
                            <span className="text-xs text-muted-foreground font-medium">{totalSources} sources</span>
                            <Select value={sortMode} onValueChange={val => setSortMode(val as SortMode)}>
                                <SelectTrigger className="h-7 w-[140px] text-xs">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="grade">Evidence Grade</SelectItem>
                                    <SelectItem value="recent">Most Recent</SelectItem>
                                    <SelectItem value="relevance">Relevance</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex-1 overflow-y-auto px-4 pb-4">
                            <div className="mb-3">
                                <h3 className="text-muted-foreground text-sm font-semibold">All Sources</h3>
                                <p className="text-muted-foreground mt-1 text-xs">
                                    {totalSources} source{totalSources !== 1 ? "s" : ""} found across {ragData.length} search{ragData.length !== 1 ? "es" : ""}
                                </p>
                            </div>

                            {/* Knowledge Graph sources — rendered before web sources */}
                            {ragData?.some(r => r.searchType === "kb_search") && (
                                <div className="mb-3">
                                    <div className="flex items-center gap-2 mb-2 text-sm font-medium text-purple-400">
                                        <Network className="h-4 w-4" />
                                        Knowledge Graph
                                    </div>
                                    <div className="space-y-2">
                                        {ragData
                                            .filter(r => r.searchType === "kb_search")
                                            .flatMap(r => r.sources)
                                            .map((source) => (
                                                <div key={source.citationId} className="border-l-2 border-purple-500/40 pl-2">
                                                    <SourceCard source={source} isHighlighted={highlightedSourceId === source.citationId} />
                                                </div>
                                            ))}
                                    </div>
                                </div>
                            )}

                            <VirtualizedSourceCardList ragData={ragData?.filter(r => r.searchType !== "kb_search")} highlightedSourceId={highlightedSourceId} sortMode={sortMode} />
                        </div>
                    </TabsContent>
                </Tabs>
            )}

            {/* Sources Unavailable — shown when pipeline reports MCP failures */}
            {pipelineErrors?.mcp_failures && pipelineErrors.mcp_failures.length > 0 && (
                <div className="mt-4 border-t border-border/50 px-4 pt-3 pb-3 flex-shrink-0">
                    <h3 className="text-sm font-semibold text-amber-600 dark:text-amber-400 mb-2 flex items-center gap-1.5">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        {pipelineErrors.mcp_failures.length} Source{pipelineErrors.mcp_failures.length !== 1 ? "s" : ""} Unavailable
                    </h3>
                    <div className="space-y-1.5">
                        {pipelineErrors.mcp_failures.map((failure, idx) => (
                            <div
                                key={idx}
                                className="flex items-start gap-2 rounded bg-amber-500/10 px-2 py-1.5 text-xs"
                            >
                                <span className="mt-0.5 h-2 w-2 flex-shrink-0 rounded-full bg-amber-500" />
                                <div className="min-w-0 flex-1">
                                    <span className="block font-medium capitalize">{failure.server.replace(/_/g, " ")}</span>
                                    <span className="text-muted-foreground block">
                                        {failure.error_category.replace(/_/g, " ")}
                                        {failure.is_retryable && " — retryable"}
                                    </span>
                                    {failure.recovery_hint && (
                                        <span className="text-muted-foreground/80 block mt-0.5 italic">
                                            {failure.recovery_hint}
                                        </span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};
