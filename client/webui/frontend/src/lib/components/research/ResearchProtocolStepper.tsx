/**
 * Research Protocol Stepper Component
 *
 * Displays the MedExpert 12-step research protocol progress.
 * Shows each step's status (pending/active/complete/error) with details.
 */

import React, { useState } from "react";
import {
  Sparkles,
  GitBranch,
  Send,
  Inbox,
  Eye,
  RefreshCw,
  CheckSquare,
  Users,
  FileText,
  Shield,
  Pencil,
  Database,
  ChevronDown,
  ChevronUp,
  CheckCircle,
  AlertTriangle,
  Loader2,
} from "lucide-react";

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
}

interface StepInfo {
  step: number;
  name: string;
  label: string;
  icon: typeof Sparkles;
}

const PROTOCOL_STEPS: StepInfo[] = [
  { step: 0, name: "SEED", label: "Loading intelligence", icon: Sparkles },
  { step: 1, name: "DECOMPOSE", label: "Analyzing query", icon: GitBranch },
  { step: 2, name: "DELEGATE", label: "Assigning specialists", icon: Send },
  { step: 3, name: "COLLECT", label: "Gathering evidence", icon: Inbox },
  { step: 4, name: "REFLECT", label: "Identifying gaps", icon: Eye },
  { step: 5, name: "RE_QUERY", label: "Follow-up queries", icon: RefreshCw },
  { step: 6, name: "VALIDATE", label: "Checking completeness", icon: CheckSquare },
  { step: 7, name: "ADVISORY", label: "Advisory board review", icon: Users },
  { step: 8, name: "SYNTHESIZE", label: "Generating report", icon: FileText },
  { step: 9, name: "VERIFY", label: "Verifying claims", icon: Shield },
  { step: 10, name: "REVISE", label: "Revising if needed", icon: Pencil },
  { step: 11, name: "PERSIST", label: "Saving results", icon: Database },
];

type StepStatus = "pending" | "active" | "complete" | "skipped" | "error";

function getStepStatus(
  stepIndex: number,
  currentStep: number,
  isComplete: boolean,
  verdict?: string | null
): StepStatus {
  if (isComplete) return "complete";
  if (stepIndex < currentStep) return "complete";
  if (stepIndex === currentStep) return "active";
  // Step 10 (REVISE) is skipped if verification passed
  if (stepIndex === 10 && currentStep > 10 && verdict && verdict !== "CRITICAL_ISSUES") {
    return "skipped";
  }
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
    line: "bg-red-300",
  },
};

export function ResearchProtocolStepper({
  progress,
  isComplete = false,
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
          {progress.verification_verdict && (
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
          )}
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
              progress.verification_verdict
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
        </div>
      )}
    </div>
  );
}
