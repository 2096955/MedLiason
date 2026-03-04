/**
 * ResearchInconclusivePanel
 *
 * Renders a structured error display when the research pipeline withholds
 * a report (e.g., citation verification failure, all sources failed).
 * Falls back to raw text rendering when the message does not match the
 * expected "Research Inconclusive" pattern.
 */

import React, { useMemo } from "react";
import { AlertTriangle, RefreshCw, ServerCrash, ListChecks } from "lucide-react";

import type { PipelineErrorData, MCPFailure } from "@/lib/types";

interface ResearchInconclusivePanelProps {
    /** The raw message text from the orchestrator */
    messageText: string;
    /** Pipeline error data for structured display */
    pipelineErrors?: PipelineErrorData | null;
    /** Callback to retry the query */
    onRetry?: () => void;
}

/** Map pipeline_error codes to human-readable explanations */
const PIPELINE_ERROR_MESSAGES: Record<string, string> = {
    citation_map_empty: "Sources could not be collected for citation verification.",
    all_sources_failed: "All specialist data sources were unavailable during this query.",
    verification_skipped: "Claim verification was skipped due to missing citation data.",
};

const DEFAULT_PIPELINE_MESSAGE = "The research pipeline encountered an issue preventing report delivery.";

/** Human-readable labels for MCP error categories */
const ERROR_CATEGORY_LABELS: Record<string, string> = {
    rate_limited: "Rate limited",
    service_unavailable: "Service unavailable",
    circuit_open: "Circuit breaker open",
    auth_error: "Authentication error",
    not_found: "Not found",
    timeout: "Request timeout",
};

/**
 * Parse failed claims from the orchestrator's inconclusive message text.
 * Looks for bullet lines (- or *) between "Research Inconclusive" and
 * "No report delivered" (or end of text).
 */
function parseFailedClaims(text: string): string[] {
    // Find the region after "Research Inconclusive" heading
    const inconclusiveIdx = text.indexOf("Research Inconclusive");
    if (inconclusiveIdx === -1) return [];

    const afterHeading = text.slice(inconclusiveIdx + "Research Inconclusive".length);

    // Find end boundary: "No report delivered" or end of text
    const endIdx = afterHeading.indexOf("No report delivered");
    const region = endIdx !== -1 ? afterHeading.slice(0, endIdx) : afterHeading;

    const claims: string[] = [];
    const lines = region.split("\n");
    for (const line of lines) {
        const trimmed = line.trim();
        // Match lines starting with - or * (markdown bullet)
        if (/^[-*]\s+/.test(trimmed)) {
            const claim = trimmed.replace(/^[-*]\s+/, "").trim();
            if (claim.length > 0) {
                claims.push(claim);
            }
        }
    }
    return claims;
}

/** Render a single MCP failure row */
const MCPFailureRow: React.FC<{ failure: MCPFailure }> = ({ failure }) => {
    const categoryLabel = ERROR_CATEGORY_LABELS[failure.error_category] || failure.error_category;
    return (
        <li className="flex items-center justify-between gap-2 text-sm">
            <span className="font-medium text-amber-800 dark:text-amber-200">{failure.server}</span>
            <span className="flex items-center gap-1.5">
                <span className="text-amber-700 dark:text-amber-300">{categoryLabel}</span>
                {failure.is_retryable && (
                    <span className="rounded bg-amber-200 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-800 dark:text-amber-200">
                        Retryable
                    </span>
                )}
            </span>
        </li>
    );
};

export const ResearchInconclusivePanel: React.FC<ResearchInconclusivePanelProps> = ({ messageText, pipelineErrors, onRetry }) => {
    const isInconclusivePattern = messageText.includes("Research Inconclusive");

    const failedClaims = useMemo(() => parseFailedClaims(messageText), [messageText]);

    const explanationText = useMemo(() => {
        if (pipelineErrors?.pipeline_error) {
            return PIPELINE_ERROR_MESSAGES[pipelineErrors.pipeline_error] || DEFAULT_PIPELINE_MESSAGE;
        }
        if (pipelineErrors?.aggregate_status === "all_failed") {
            return PIPELINE_ERROR_MESSAGES.all_sources_failed;
        }
        if (pipelineErrors?.verification_status === "skipped") {
            return PIPELINE_ERROR_MESSAGES.verification_skipped;
        }
        return DEFAULT_PIPELINE_MESSAGE;
    }, [pipelineErrors]);

    const mcpFailures = pipelineErrors?.mcp_failures ?? [];

    // Fallback: render raw text if message does not match the expected pattern
    if (!isInconclusivePattern) {
        return <div className="whitespace-pre-wrap">{messageText}</div>;
    }

    return (
        <div role="alert" className="space-y-4 rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/30">
            {/* Header */}
            <div className="flex items-center gap-2">
                <AlertTriangle aria-hidden="true" className="h-5 w-5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
                <p className="text-base font-semibold text-amber-800 dark:text-amber-200">Research Inconclusive</p>
            </div>

            {/* Explanation */}
            <p className="text-sm text-amber-700 dark:text-amber-300">{explanationText}</p>

            {/* MCP failures section */}
            {mcpFailures.length > 0 && (
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <ServerCrash aria-hidden="true" className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                        <span className="text-sm font-medium text-amber-800 dark:text-amber-200">Unavailable Sources</span>
                    </div>
                    <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-100/50 p-3 dark:border-amber-700 dark:bg-amber-900/30">
                        {mcpFailures.map((failure, idx) => (
                            <MCPFailureRow key={`${failure.server}-${idx}`} failure={failure} />
                        ))}
                    </ul>
                </div>
            )}

            {/* Failed claims section */}
            {failedClaims.length > 0 && (
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <ListChecks aria-hidden="true" className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                        <span className="text-sm font-medium text-amber-800 dark:text-amber-200">Unverified Claims</span>
                    </div>
                    <ul className="list-inside list-disc space-y-1 pl-1 text-sm text-amber-700 dark:text-amber-300">
                        {failedClaims.map((claim, idx) => (
                            <li key={idx}>{claim}</li>
                        ))}
                    </ul>
                </div>
            )}

            {/* Retry button */}
            {onRetry && (
                <button
                    onClick={onRetry}
                    className="inline-flex items-center gap-2 rounded-md border border-amber-300 bg-amber-100 px-3 py-1.5 text-sm font-medium text-amber-800 transition-colors hover:bg-amber-200 dark:border-amber-700 dark:bg-amber-900/50 dark:text-amber-200 dark:hover:bg-amber-900/80"
                >
                    <RefreshCw aria-hidden="true" className="h-4 w-4" />
                    Retry Research
                </button>
            )}

            {/* Disclaimer */}
            <p className="text-xs text-amber-600/80 dark:text-amber-400">
                MedExpert could not verify accuracy of all claims. No report was delivered to protect information quality.
            </p>
        </div>
    );
};

export default ResearchInconclusivePanel;
