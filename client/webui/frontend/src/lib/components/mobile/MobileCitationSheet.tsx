import { ExternalLink, FileText, CheckCircle, AlertTriangle, HelpCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { MobileBottomSheet } from "./MobileBottomSheet";
import type { RAGSource } from "@/lib/types/fe";

const GRADE_COLORS: Record<string, string> = {
    High: "bg-green-500",
    Moderate: "bg-yellow-500",
    Low: "bg-orange-500",
    "Very Low": "bg-red-500",
};

const VERIFICATION_CONFIG: Record<string, { icon: typeof CheckCircle; color: string; label: string }> = {
    verified: { icon: CheckCircle, color: "text-green-500", label: "Verified" },
    flagged: { icon: AlertTriangle, color: "text-amber-500", label: "Flagged" },
    unverified: { icon: HelpCircle, color: "text-gray-400", label: "Unverified" },
};

interface MobileCitationSheetProps {
    open: boolean;
    onClose: () => void;
    sources: RAGSource[];
    highlightedId?: string;
}

function getDomain(url: string): string {
    try {
        return new URL(url).hostname.replace(/^www\./, "");
    } catch {
        return url;
    }
}

function getFavicon(url: string): string | null {
    try {
        const domain = new URL(url).hostname;
        return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`;
    } catch {
        return null;
    }
}

export function MobileCitationSheet({ open, onClose, sources, highlightedId }: MobileCitationSheetProps) {
    if (!sources || sources.length === 0) return null;

    return (
        <MobileBottomSheet open={open} onClose={onClose} title={`${sources.length} Sources`} height="full">
            <div className="divide-y divide-border">
                {sources.map((source, i) => {
                    const url = source.sourceUrl || source.metadata?.link;
                    const domain = url ? getDomain(url) : null;
                    const favicon = url ? getFavicon(url) : null;
                    const title = source.metadata?.title || source.filename || `Source ${i + 1}`;
                    const preview = source.contentPreview;
                    const grade = source.evidenceGrade || source.metadata?.evidence_grade as string | undefined;
                    const verification = source.verificationStatus || source.metadata?.verification_status as string | undefined;
                    const verConfig = verification ? VERIFICATION_CONFIG[verification] : null;
                    const VerIcon = verConfig?.icon;
                    const isHighlighted = source.citationId === highlightedId;

                    return (
                        <div
                            key={source.citationId || `source-${i}`}
                            className={cn(
                                "px-4 py-3 transition-colors",
                                isHighlighted && "bg-[var(--color-brand-wMain)]/10"
                            )}
                        >
                            <div className="flex items-start gap-3">
                                {favicon ? (
                                    <img src={favicon} alt="" className="mt-0.5 h-5 w-5 rounded-full" />
                                ) : (
                                    <FileText className="mt-0.5 h-5 w-5 text-muted-foreground" />
                                )}

                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-1.5">
                                        {grade && (
                                            <span
                                                className={cn("inline-block h-2 w-2 rounded-full", GRADE_COLORS[grade])}
                                                title={`Evidence: ${grade}`}
                                            />
                                        )}
                                        <span className="truncate text-sm font-medium text-foreground">{title}</span>
                                        {VerIcon && (
                                            <VerIcon className={cn("h-3.5 w-3.5 shrink-0", verConfig!.color)} />
                                        )}
                                    </div>

                                    {domain && (
                                        <span className="text-xs text-muted-foreground">{domain}</span>
                                    )}

                                    {preview && (
                                        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                                            {preview}
                                        </p>
                                    )}

                                    {source.relevanceScore != null && source.relevanceScore > 0 && (
                                        <div className="mt-1.5 flex items-center gap-1.5">
                                            <div className="h-1 w-16 rounded-full bg-muted">
                                                <div
                                                    className="h-1 rounded-full bg-[var(--color-brand-wMain)]"
                                                    style={{ width: `${Math.round(source.relevanceScore * 100)}%` }}
                                                />
                                            </div>
                                            <span className="text-[10px] text-muted-foreground">
                                                {Math.round(source.relevanceScore * 100)}%
                                            </span>
                                        </div>
                                    )}
                                </div>

                                {url && (
                                    <a
                                        href={url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="mt-0.5 shrink-0 rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-muted"
                                        aria-label="Open source"
                                    >
                                        <ExternalLink className="h-4 w-4" />
                                    </a>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </MobileBottomSheet>
    );
}
