import React from "react";
import { X } from "lucide-react";

import { Button } from "@/lib/components/ui";
import { cn } from "@/lib/utils";

const LABEL_COLORS: Record<string, string> = {
    Disease: "bg-red-500/20 text-red-400 border-red-500/30",
    Drug: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    Gene: "bg-green-500/20 text-green-400 border-green-500/30",
    Study: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    Session: "bg-purple-500/20 text-purple-400 border-purple-500/30",
    Specialist: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
};

const DEFAULT_LABEL_COLOR = "bg-gray-500/20 text-gray-400 border-gray-500/30";

interface EntityDetailPanelProps {
    nodeId: string;
    nodeData: Record<string, unknown>;
    onClose: () => void;
    onViewSource?: (identifier: { type: "pmid" | "nct_id" | "doi"; value: string }) => void;
    onSearchSources?: (entityName: string) => void;
}

function formatValue(value: unknown): string {
    if (value === null || value === undefined) return "N/A";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
}

const HIDDEN_KEYS = new Set(["id", "label", "nodeLabel"]);

const EntityDetailPanel: React.FC<EntityDetailPanelProps> = ({ nodeId, nodeData, onClose, onViewSource, onSearchSources }) => {
    const name = (nodeData.label as string) || nodeId;
    const nodeLabel = (nodeData.nodeLabel as string) || "Unknown";
    const labels = nodeLabel.split(",").map((l) => l.trim());
    const description = nodeData.description as string | undefined;

    // Filter out internal keys and empty values to show user-relevant properties
    const properties = Object.entries(nodeData)
        .filter(([key]) => !HIDDEN_KEYS.has(key) && key !== "description")
        .filter(([, value]) => value !== "" && value !== null && value !== undefined);

    const rawPmid = nodeData.pmid as string | undefined;
    const rawNctId = nodeData.nct_id as string | undefined;
    const pmid = rawPmid && /^\d+$/.test(rawPmid) ? rawPmid : null;
    // Assumes NCT-prefixed IDs only. ISRCTN/EUCTR identifiers from other registries
    // may appear in nct_id if source collection expands — extend regex if needed.
    const nctId = rawNctId && /^NCT\d+$/i.test(rawNctId) ? rawNctId : null;
    const isPartial = nodeData.partial === true;

    return (
        <div className="flex h-full w-80 flex-col border-l border-border bg-background">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <h3 className="truncate text-sm font-semibold text-foreground">{name}</h3>
                <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
                    <X className="h-4 w-4" />
                </Button>
            </div>

            {/* Labels */}
            <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-3">
                {labels.map((lbl) => (
                    <span
                        key={lbl}
                        className={cn("rounded-full border px-2.5 py-0.5 text-xs font-medium", LABEL_COLORS[lbl] || DEFAULT_LABEL_COLOR)}
                    >
                        {lbl}
                    </span>
                ))}
            </div>

            {/* Partial study indicator */}
            {isPartial && (
                <div className="border-b border-border px-4 py-2">
                    <span className="text-xs text-amber-500">Stub — referenced but not directly retrieved</span>
                </div>
            )}

            {/* External links for Study nodes */}
            {nodeLabel === "Study" && (pmid || nctId) && (
                <div className="border-b border-border px-4 py-3">
                    {pmid && (
                        <a
                            href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-blue-500 hover:underline"
                        >
                            View on PubMed
                        </a>
                    )}
                    {nctId && (
                        <a
                            href={`https://clinicaltrials.gov/study/${nctId}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-3 text-xs text-blue-500 hover:underline"
                        >
                            View on ClinicalTrials.gov
                        </a>
                    )}
                    {onViewSource && (
                        <Button
                            variant="outline"
                            size="sm"
                            className="ml-3"
                            onClick={() => onViewSource({
                                type: pmid ? "pmid" : "nct_id",
                                value: (pmid || nctId)!,
                            })}
                        >
                            View in Sources
                        </Button>
                    )}
                </div>
            )}

            {/* Search Sources action for entity nodes (Disease, Drug, Gene) */}
            {onSearchSources && !pmid && !nctId && (
                <div className="border-b border-border px-4 py-3">
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onSearchSources(name)}
                    >
                        Search Sources
                    </Button>
                </div>
            )}

            {/* Description */}
            {description && (
                <div className="border-b border-border px-4 py-3">
                    <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
                </div>
            )}

            {/* Properties */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
                <h4 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">Properties</h4>
                {properties.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No additional properties</p>
                ) : (
                    <dl className="space-y-2">
                        {properties.map(([key, value]) => (
                            <div key={key}>
                                <dt className="text-xs font-medium text-muted-foreground">{key}</dt>
                                <dd className="mt-0.5 break-all text-xs text-foreground">{formatValue(value)}</dd>
                            </div>
                        ))}
                    </dl>
                )}
            </div>

            {/* Footer */}
            <div className="border-t border-border px-4 py-2">
                <p className="text-xs text-muted-foreground">ID: {nodeId}</p>
            </div>
        </div>
    );
};

export default EntityDetailPanel;
