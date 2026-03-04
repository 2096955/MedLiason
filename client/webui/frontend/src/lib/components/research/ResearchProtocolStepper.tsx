/**
 * Research Protocol Stepper Component
 *
 * Displays the MedExpert 7-step research protocol progress.
 * Shows each step's status (pending/active/complete/error) with details.
 */

import { useState } from "react";
import {
  Sparkles,
  GitBranch,
  Send,
  Inbox,
  FileText,
  Shield,
  Database,
  ChevronDown,
  ChevronUp,
  CheckCircle,
  AlertTriangle,
  Loader2,
  RefreshCw,
  Clock,
} from "lucide-react";
import type { PipelineErrorData } from "@/lib/types";

export interface ResearchProtocolProgressData {
  type: "research_protocol_progress";
  step: number;
  step_name: string;
  total_steps: number;
  detail: string;
  coverage_pct?: number | null;
  gvr_cycle?: number;
  verification_verdict?: string | null;
}

interface ResearchProtocolStepperProps {
  progress: ResearchProtocolProgressData;
  isComplete?: boolean;
  pipelineErrors?: PipelineErrorData | null;
}

interface StepInfo {
  step: number;
  name: string;
  label: string;
  icon: typeof Sparkles;
}

const PROTOCOL_STEPS: StepInfo[] = [
  { step: 0, name: "SEED", label: "Loading intelligence", icon: Sparkles },
  { step: 1, name: "PLAN", label: "Analyzing query", icon: GitBranch },
  { step: 2, name: "DELEGATE", label: "Querying specialists", icon: Send },
  { step: 3, name: "COLLECT", label: "Gathering & publishing evidence", icon: Inbox },
  { step: 4, name: "SYNTHESIZE", label: "Generating report", icon: FileText },
  { step: 5, name: "VERIFY", label: "Verifying claims", icon: Shield },
  { step: 6, name: "PERSIST", label: "Saving results", icon: Database },
];

type StepStatus = "pending" | "active" | "complete" | "skipped" | "error" | "warning";

function getStepStatus(
  stepIndex: number,
  currentStep: number,
  isComplete: boolean,
  _verdict?: string | null,
  pipelineErrors?: PipelineErrorData | null
): StepStatus {
  // Helper: check pipeline errors for specific steps
  const collectHasError = stepIndex === 3 && pipelineErrors?.aggregate_status === "all_failed";
  const collectHasWarning = stepIndex === 3 && pipelineErrors?.aggregate_status === "partial";
  const verifySkipped = stepIndex === 5 && pipelineErrors?.verification_status === "skipped";

  if (isComplete || stepIndex < currentStep) {
    if (collectHasError) return "error";
    if (collectHasWarning || verifySkipped) return "warning";
    if (stepIndex < currentStep || isComplete) return "complete";
  }
  if (stepIndex === currentStep) return "active";
  return "pending";
}

const statusStyles: Record<StepStatus, { dot: string; text: string; line: string }> = {
  pending: {
    dot: "bg-gray-300 dark:bg-gray-600",
    text: "text-gray-400 dark:text-gray-500",
    line: "bg-gray-200 dark:bg-gray-700",
  },
  active: {
    dot: "bg-blue-500 animate-pulse",
    text: "text-blue-600 dark:text-blue-400 font-medium",
    line: "bg-gray-200 dark:bg-gray-700",
  },
  complete: {
    dot: "bg-green-500",
    text: "text-gray-700 dark:text-gray-300",
    line: "bg-green-500",
  },
  skipped: {
    dot: "bg-gray-400",
    text: "text-gray-400 dark:text-gray-500 line-through",
    line: "bg-gray-300 dark:bg-gray-600",
  },
  error: {
    dot: "bg-red-500",
    text: "text-red-600 dark:text-red-400",
    line: "bg-red-300 dark:bg-red-700",
  },
  warning: {
    dot: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
    line: "bg-amber-300 dark:bg-amber-600",
  },
};

export function ResearchProtocolStepper({
  progress,
  isComplete = false,
  pipelineErrors,
}: ResearchProtocolStepperProps) {
  const [expanded, setExpanded] = useState(true);

  const progressPct = isComplete
    ? 100
    : Math.round(((progress.step + 1) / progress.total_steps) * 100);

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden text-sm">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label="Toggle research protocol steps"
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors"
      >
        <div className="flex items-center gap-2">
          {isComplete ? (
            <CheckCircle className="w-4 h-4 text-green-500" />
          ) : (
            <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
          )}
          <span className="font-medium text-gray-900 dark:text-gray-100">
            {isComplete ? "Research complete" : `Research in progress — ${progressPct}%`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {progress.coverage_pct != null && (
            <span className="text-xs text-gray-500">
              {Math.round(progress.coverage_pct * 100)}% coverage
            </span>
          )}
          {pipelineErrors?.verification_status === "skipped" ? (
            <span
              className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-300"
              title={`Pipeline issue: ${pipelineErrors?.pipeline_error || "verification skipped"}`}
            >
              Skipped
            </span>
          ) : progress.verification_verdict ? (
            <span
              className={`text-xs px-1.5 py-0.5 rounded ${
                progress.verification_verdict === "PASS"
                  ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                  : progress.verification_verdict === "MINOR_ISSUES"
                    ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300"
                    : "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300"
              }`}
            >
              {progress.verification_verdict === "PASS"
                ? "Verified"
                : progress.verification_verdict === "MINOR_ISSUES"
                  ? "Minor issues"
                  : "Revising..."}
            </span>
          ) : null}
          {expanded ? (
            <ChevronUp className="w-4 h-4 text-gray-400" />
          ) : (
            <ChevronDown className="w-4 h-4 text-gray-400" />
          )}
        </div>
      </button>

      {/* Progress bar */}
      <div className="h-1 bg-gray-100 dark:bg-gray-700">
        <div
          className="h-full bg-blue-500 transition-all duration-500"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      {/* Steps */}
      {expanded && (
        <div className="px-3 py-2 space-y-0">
          {PROTOCOL_STEPS.map((stepInfo, idx) => {
            const status = getStepStatus(
              idx,
              progress.step,
              isComplete,
              progress.verification_verdict,
              pipelineErrors
            );
            const styles = statusStyles[status];
            const Icon = stepInfo.icon;
            const isActive = status === "active";

            return (
              <div key={stepInfo.name} className="flex items-start gap-2 relative">
                {/* Vertical connector line */}
                {idx < PROTOCOL_STEPS.length - 1 && (
                  <div
                    className={`absolute left-[9px] top-5 w-0.5 h-4 ${styles.line}`}
                  />
                )}
                {/* Status dot */}
                <div className="flex-shrink-0 mt-1">
                  {status === "complete" ? (
                    <CheckCircle className="w-[18px] h-[18px] text-green-500" />
                  ) : status === "error" ? (
                    <AlertTriangle className="w-[18px] h-[18px] text-red-500" />
                  ) : status === "warning" ? (
                    <AlertTriangle className="w-[18px] h-[18px] text-amber-500" />
                  ) : (
                    <div className={`w-[18px] h-[18px] rounded-full ${styles.dot} flex items-center justify-center`}>
                      {isActive && <Icon className="w-2.5 h-2.5 text-white" />}
                    </div>
                  )}
                </div>
                {/* Label + detail */}
                <div className={`flex-1 pb-1 ${styles.text}`}>
                  <span className="text-xs">{stepInfo.label}</span>
                  {isActive && progress.detail && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 leading-tight">
                      {progress.detail}
                    </p>
                  )}
                  {idx === 6 && status === "complete" && progress.coverage_pct != null && (
                    <span className="text-xs text-gray-500 ml-1">
                      ({Math.round(progress.coverage_pct * 100)}%)
                    </span>
                  )}
                  {/* MCP failure detail for step 3 (COLLECT) */}
                  {idx === 3 && (status === "error" || status === "warning") &&
                    pipelineErrors?.mcp_failures && pipelineErrors.mcp_failures.length > 0 && (
                    <div className="mt-1 text-xs space-y-0.5">
                      <span className="font-medium text-amber-600 dark:text-amber-400">
                        {pipelineErrors.aggregate_status === "all_failed"
                          ? "All sources failed"
                          : `${pipelineErrors.mcp_failures.length} source(s) unavailable`}
                      </span>
                      {pipelineErrors.mcp_failures.map((f, i) => (
                        <div key={i} className="flex items-center gap-1 pl-2 text-muted-foreground">
                          <span className="h-1 w-1 rounded-full bg-amber-500 flex-shrink-0" />
                          <span>{f.server}: {f.error_category.replace(/_/g, " ")}</span>
                          {f.is_retryable && <span className="opacity-60">(retryable)</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {/* GVR cycle indicator */}
          {(progress.gvr_cycle ?? 0) > 0 && (
            <div className="flex items-center gap-1.5 pt-1 text-xs text-amber-600 dark:text-amber-400">
              <RefreshCw className="w-3 h-3" />
              <span>Revision cycle {progress.gvr_cycle}</span>
            </div>
          )}

          {/* Stall warning banner */}
          {pipelineErrors?.stalled && !isComplete && (
            <div role="alert" className="flex items-center gap-2 px-2 py-1.5 mt-1 rounded-md bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800">
              <Clock className="w-4 h-4 text-amber-500 flex-shrink-0" aria-hidden="true" />
              <div className="flex-1">
                <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                  Research may have stalled
                </p>
                <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">
                  No progress received for 60 seconds. The agent may be processing a complex query.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
