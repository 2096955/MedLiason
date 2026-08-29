import { Activity, CheckCircle, AlertTriangle, Clock, ChevronRight, Shield, Phone } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { MobileBottomSheet } from "./MobileBottomSheet";

interface TriageStage {
    name: string;
    icon: LucideIcon;
}

const STAGES: TriageStage[] = [
    { name: "Intake", icon: Activity },
    { name: "Routing", icon: ChevronRight },
    { name: "Specialists", icon: Shield },
    { name: "Evaluation", icon: CheckCircle },
    { name: "Action", icon: Phone },
];

interface SpecialistVerdict {
    specialist: string;
    diagnosis: string;
    confidence: number;
    tier: number;
}

interface TriageProgressData {
    stage: number;
    stage_name: string;
    detail?: string;
    specialist_verdicts?: SpecialistVerdict[];
    consensus?: {
        diagnosis: string;
        mean_confidence: number;
        supporting: string[];
        dissenting: string[];
    };
    evaluation?: {
        confidence: number;
        flag_for_review: boolean;
        emergency_confirmed: boolean;
    };
    nba?: {
        route: string;
        urgency: string;
        self_care_advice?: string;
        disclaimer?: string;
    };
    emergency_override?: boolean;
    error?: string;
}

interface MobileTriageViewProps {
    open: boolean;
    onClose: () => void;
    progress: TriageProgressData | null;
}

function ConfidenceBar({ value, size = "sm" }: { value: number; size?: "sm" | "lg" }) {
    const color =
        value >= 70 ? "bg-emerald-500" : value >= 40 ? "bg-amber-500" : "bg-red-500";
    const h = size === "lg" ? "h-2" : "h-1.5";
    return (
        <div className={cn("w-full rounded-full bg-muted", h)}>
            <div
                className={cn("rounded-full transition-all duration-500", color, h)}
                style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
            />
        </div>
    );
}

function UrgencyBadge({ urgency }: { urgency: string }) {
    const config: Record<string, { bg: string; text: string }> = {
        immediate: { bg: "bg-red-100 dark:bg-red-900/40", text: "text-red-700 dark:text-red-400" },
        urgent: { bg: "bg-amber-100 dark:bg-amber-900/40", text: "text-amber-700 dark:text-amber-400" },
        routine: { bg: "bg-blue-100 dark:bg-blue-900/40", text: "text-blue-700 dark:text-blue-400" },
        low: { bg: "bg-emerald-100 dark:bg-emerald-900/40", text: "text-emerald-700 dark:text-emerald-400" },
        minimal: { bg: "bg-gray-100 dark:bg-gray-800", text: "text-gray-700 dark:text-gray-400" },
    };
    const style = config[urgency] || config.routine;
    return (
        <span className={cn("rounded-full px-2.5 py-1 text-xs font-semibold uppercase", style.bg, style.text)}>
            {urgency}
        </span>
    );
}

function RouteLabel({ route }: { route: string }) {
    const labels: Record<string, string> = {
        emergency_services: "Call Emergency Services",
        hospital: "Go to Hospital",
        gp_visit: "See Your GP",
        specialist_referral: "Specialist Referral",
        pharmacist: "Visit a Pharmacist",
        self_care: "Self-Care at Home",
    };
    return <span>{labels[route] || route}</span>;
}

export function MobileTriageView({ open, onClose, progress }: MobileTriageViewProps) {
    if (!progress) return null;

    const currentStage = progress.stage;

    return (
        <MobileBottomSheet open={open} onClose={onClose} title="Medical Triage" height="full">
            <div className="px-4 py-4">
                {/* Stepper */}
                <div className="mb-6 flex items-center justify-between">
                    {STAGES.map((stage, i) => {
                        const Icon = stage.icon;
                        const isComplete = i < currentStage;
                        const isCurrent = i === currentStage;
                        return (
                            <div key={stage.name} className="flex flex-1 flex-col items-center">
                                <div
                                    className={cn(
                                        "flex h-8 w-8 items-center justify-center rounded-full transition-colors",
                                        isComplete
                                            ? "bg-emerald-500 text-white"
                                            : isCurrent
                                              ? "bg-[var(--color-brand-wMain)] text-white"
                                              : "bg-muted text-muted-foreground"
                                    )}
                                >
                                    {isComplete ? (
                                        <CheckCircle className="h-4 w-4" />
                                    ) : (
                                        <Icon className={cn("h-4 w-4", isCurrent && "animate-pulse")} />
                                    )}
                                </div>
                                <span
                                    className={cn(
                                        "mt-1 text-[10px]",
                                        isCurrent ? "font-semibold text-foreground" : "text-muted-foreground"
                                    )}
                                >
                                    {stage.name}
                                </span>
                            </div>
                        );
                    })}
                </div>

                {/* Status */}
                <div className="mb-4 rounded-lg bg-muted/50 p-3">
                    <div className="flex items-center gap-2">
                        <Clock className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-medium text-foreground">{progress.stage_name}</span>
                    </div>
                    {progress.detail && (
                        <p className="mt-1 text-xs text-muted-foreground">{progress.detail}</p>
                    )}
                </div>

                {/* Emergency override */}
                {progress.emergency_override && (
                    <div className="mb-4 rounded-lg border-2 border-red-500 bg-red-50 p-3 dark:bg-red-950/30">
                        <div className="flex items-center gap-2">
                            <AlertTriangle className="h-5 w-5 text-red-600" />
                            <span className="text-sm font-bold text-red-700 dark:text-red-400">
                                Emergency Detected
                            </span>
                        </div>
                        <p className="mt-1 text-xs text-red-600 dark:text-red-400">
                            Fast-tracking to emergency care routing
                        </p>
                    </div>
                )}

                {/* Specialist verdicts */}
                {progress.specialist_verdicts && progress.specialist_verdicts.length > 0 && (
                    <div className="mb-4">
                        <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                            Specialist Panel
                        </h3>
                        <div className="space-y-2">
                            {progress.specialist_verdicts.map((v) => (
                                <div key={v.specialist} className="rounded-lg border border-border p-3">
                                    <div className="flex items-center justify-between">
                                        <span className="text-sm font-medium capitalize text-foreground">
                                            {v.specialist.replace(/_/g, " ")}
                                        </span>
                                        <span
                                            className={cn(
                                                "text-xs font-semibold",
                                                v.tier === 1 ? "text-blue-600" : "text-muted-foreground"
                                            )}
                                        >
                                            Tier {v.tier}
                                        </span>
                                    </div>
                                    <p className="mt-1 text-xs text-muted-foreground">{v.diagnosis}</p>
                                    <div className="mt-2 flex items-center gap-2">
                                        <ConfidenceBar value={v.confidence} />
                                        <span className="text-[10px] font-medium text-muted-foreground">
                                            {v.confidence}%
                                        </span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Consensus */}
                {progress.consensus && (
                    <div className="mb-4 rounded-lg border-2 border-[var(--color-brand-wMain)]/30 bg-[var(--color-brand-wMain)]/5 p-3">
                        <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                            Consensus Diagnosis
                        </h3>
                        <p className="text-sm font-semibold text-foreground">{progress.consensus.diagnosis}</p>
                        <div className="mt-2">
                            <ConfidenceBar value={progress.consensus.mean_confidence} size="lg" />
                            <span className="mt-1 block text-xs text-muted-foreground">
                                {progress.consensus.mean_confidence}% confidence —{" "}
                                {progress.consensus.supporting.length} supporting
                                {progress.consensus.dissenting.length > 0 &&
                                    `, ${progress.consensus.dissenting.length} dissenting`}
                            </span>
                        </div>
                    </div>
                )}

                {/* NBA (Next Best Action) */}
                {progress.nba && (
                    <div className="mb-4">
                        <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                            Recommended Action
                        </h3>
                        <div className="rounded-xl border-2 border-border bg-background p-4 shadow-sm">
                            <div className="flex items-center justify-between">
                                <span className="text-base font-bold text-foreground">
                                    <RouteLabel route={progress.nba.route} />
                                </span>
                                <UrgencyBadge urgency={progress.nba.urgency} />
                            </div>
                            {progress.nba.self_care_advice && (
                                <p className="mt-2 text-sm text-muted-foreground">
                                    {progress.nba.self_care_advice}
                                </p>
                            )}
                            {progress.nba.disclaimer && (
                                <p className="mt-3 rounded border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:bg-amber-950/20 dark:text-amber-400">
                                    {progress.nba.disclaimer}
                                </p>
                            )}
                        </div>
                    </div>
                )}

                {/* Error */}
                {progress.error && (
                    <div className="rounded-lg bg-destructive/10 p-3">
                        <div className="flex items-center gap-2">
                            <AlertTriangle className="h-4 w-4 text-destructive" />
                            <span className="text-sm font-medium text-destructive">{progress.error}</span>
                        </div>
                    </div>
                )}
            </div>
        </MobileBottomSheet>
    );
}
