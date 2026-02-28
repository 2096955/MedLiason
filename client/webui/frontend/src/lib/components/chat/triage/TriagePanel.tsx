import React from "react";
import { AlertTriangle, Stethoscope, Users, CheckCircle, Activity } from "lucide-react";
import { Badge } from "@/lib/components/ui";
import { useChatContext } from "@/lib/hooks";

import { TriageStepper } from "./TriageStepper";
import { SpecialistVerdictCard } from "./SpecialistVerdictCard";
import { NBAActionCard } from "./NBAActionCard";
import { TriageErrorState } from "./TriageErrorState";

interface TriagePanelProps {
    isActive: boolean;
}

export const TriagePanel: React.FC<TriagePanelProps> = ({ isActive: _isActive }) => {
    const { triageProgress } = useChatContext();

    if (!triageProgress) {
        return (
            <div className="flex h-full flex-col items-center justify-center p-4">
                <Stethoscope className="text-muted-foreground mb-3 h-10 w-10" />
                <p className="text-muted-foreground text-sm font-medium">No triage in progress</p>
                <p className="text-muted-foreground mt-1 text-center text-xs">
                    Triage progress will appear here when a medical triage session is active.
                </p>
            </div>
        );
    }

    const {
        stage,
        emergency_override,
        error,
        specialist_verdicts,
        consensus,
        evaluation,
        nba,
        detail,
    } = triageProgress;

    return (
        <div className="flex h-full flex-col">
            {/* Header */}
            <div className="flex items-center justify-between border-b px-4 py-3">
                <div>
                    <h3 className="text-sm font-semibold">Medical Triage</h3>
                    <p className="text-muted-foreground text-xs">
                        {triageProgress.stage_name || "Processing..."}
                    </p>
                </div>
                {emergency_override && (
                    <Badge variant="destructive" className="gap-1 text-xs">
                        <AlertTriangle className="h-3 w-3" />
                        Emergency
                    </Badge>
                )}
            </div>

            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto">
                {/* Emergency override banner */}
                {emergency_override && stage <= 1 && (
                    <div
                        className="mx-4 mt-3 flex items-start gap-2 rounded-md border border-destructive bg-destructive/10 p-3"
                        role="alert"
                    >
                        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-destructive" />
                        <div className="flex-1">
                            <p className="text-sm font-medium text-destructive">Emergency Detected</p>
                            <p className="mt-1 text-xs text-destructive/80">
                                Emergency keywords detected. Fast-tracking to immediate action.
                            </p>
                        </div>
                    </div>
                )}

                {/* Error state */}
                {error && <TriageErrorState error={error} />}

                {/* Stepper — always visible */}
                <TriageStepper
                    currentStage={stage}
                    emergency={emergency_override}
                    error={!!error}
                    detail={detail}
                />

                <div className="space-y-4 px-4 pb-4">
                    {/* Specialist Verdicts */}
                    {specialist_verdicts && specialist_verdicts.length > 0 && (
                        <div>
                            <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                                <Users className="h-3 w-3" /> Specialist Verdicts
                            </h4>
                            <div className="grid grid-cols-1 gap-2">
                                {specialist_verdicts.map((verdict) => (
                                    <SpecialistVerdictCard
                                        key={verdict.specialist}
                                        specialist={verdict.specialist}
                                        diagnosis={verdict.diagnosis}
                                        confidence={verdict.confidence}
                                        thinking={verdict.thinking}
                                        tier={verdict.tier}
                                    />
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Consensus */}
                    {consensus && (
                        <div>
                            <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                                <CheckCircle className="h-3 w-3" /> Consensus
                            </h4>
                            <div className="rounded-md border border-border p-3">
                                <p className="text-sm font-medium">{consensus.consensus_diagnosis}</p>
                                <div className="mt-2 flex items-center gap-2">
                                    <span className="text-muted-foreground text-xs">Mean confidence:</span>
                                    <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                                        <div
                                            className={`absolute left-0 top-0 h-full rounded-full transition-all ${
                                                consensus.mean_confidence > 60
                                                    ? "bg-[hsl(var(--chart-2))]"
                                                    : consensus.mean_confidence >= 30
                                                      ? "bg-[hsl(var(--chart-4))]"
                                                      : "bg-destructive"
                                            }`}
                                            style={{ width: `${Math.min(100, Math.max(0, consensus.mean_confidence))}%` }}
                                        />
                                    </div>
                                    <span className="text-xs font-medium tabular-nums">
                                        {Math.round(consensus.mean_confidence)}%
                                    </span>
                                </div>

                                {/* Supporting specialists */}
                                {consensus.supporting_specialists.length > 0 && (
                                    <div className="mt-2">
                                        <p className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
                                            Supporting
                                        </p>
                                        <div className="mt-1 flex flex-wrap gap-1">
                                            {consensus.supporting_specialists.map((s) => (
                                                <Badge
                                                    key={s.specialist}
                                                    variant="outline"
                                                    className="border-[hsl(var(--chart-2))]/50 text-[10px] px-1.5 py-0"
                                                >
                                                    {s.specialist} ({Math.round(s.confidence)}%)
                                                </Badge>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Dissenting specialists */}
                                {consensus.dissenting_specialists.length > 0 && (
                                    <div className="mt-2">
                                        <p className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">
                                            Dissenting
                                        </p>
                                        <div className="mt-1 space-y-1">
                                            {consensus.dissenting_specialists.map((d) => (
                                                <div key={d.specialist} className="flex items-center gap-1.5 text-xs">
                                                    <Badge
                                                        variant="outline"
                                                        className="border-[hsl(var(--chart-4))]/50 text-[10px] px-1.5 py-0"
                                                    >
                                                        {d.specialist}
                                                    </Badge>
                                                    <span className="text-muted-foreground truncate">
                                                        {d.alternative_diagnosis} ({Math.round(d.confidence)}%)
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Evaluation */}
                    {evaluation && (
                        <div>
                            <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                                <Activity className="h-3 w-3" /> Evaluation
                            </h4>
                            <div className="rounded-md border border-border p-3">
                                <div className="flex items-center gap-2">
                                    <span className="text-muted-foreground text-xs">Confidence:</span>
                                    <span className="text-sm font-medium tabular-nums">
                                        {Math.round(evaluation.eval_confidence)}%
                                    </span>
                                    {evaluation.flag_for_review && (
                                        <Badge variant="destructive" className="gap-1 text-[10px] px-1.5 py-0">
                                            <AlertTriangle className="h-2.5 w-2.5" />
                                            Flagged for Review
                                        </Badge>
                                    )}
                                    {evaluation.confirmed_emergency && (
                                        <Badge variant="destructive" className="gap-1 text-[10px] px-1.5 py-0">
                                            Emergency Confirmed
                                        </Badge>
                                    )}
                                </div>
                                <p className="text-muted-foreground mt-2 text-xs leading-relaxed">
                                    {evaluation.explanation}
                                </p>
                                {evaluation.flag_reason && (
                                    <p className="mt-1 text-xs text-destructive/80">
                                        Reason: {evaluation.flag_reason}
                                    </p>
                                )}
                            </div>
                        </div>
                    )}

                    {/* NBA (Next Best Action) */}
                    {nba && (
                        <div>
                            <h4 className="text-muted-foreground mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider">
                                <Stethoscope className="h-3 w-3" /> Recommendation
                            </h4>
                            <NBAActionCard {...nba} />
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
