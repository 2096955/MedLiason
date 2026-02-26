import React from "react";
import { Check, X, Loader2, Minus } from "lucide-react";

const STEPS = ["Intake", "Routing", "Specialists", "Evaluation", "Action"] as const;

type StepStatus = "complete" | "active" | "pending" | "error" | "skipped";

interface TriageStepperProps {
    currentStage: number;
    emergency?: boolean;
    error?: boolean;
    detail?: string;
}

function getStepStatus(
    stepIndex: number,
    currentStage: number,
    totalSteps: number,
    emergency?: boolean,
    error?: boolean,
): StepStatus {
    // Map the 5-stage backend (0-4) to our 5 display steps
    // emergency_override skips stages 2-3 (Specialists, Evaluation)
    if (emergency && (stepIndex === 2 || stepIndex === 3) && currentStage <= 1) {
        return "skipped";
    }

    if (error && stepIndex === currentStage) {
        return "error";
    }

    if (stepIndex < currentStage) {
        return "complete";
    }

    if (stepIndex === currentStage) {
        // If current stage equals total stages - 1 and no error, it's the last stage completing
        if (currentStage >= totalSteps - 1 && !error) {
            return "complete";
        }
        return "active";
    }

    return "pending";
}

const StepIcon: React.FC<{ status: StepStatus }> = ({ status }) => {
    switch (status) {
        case "complete":
            return (
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-[hsl(var(--chart-2))]" aria-label="Complete">
                    <Check className="h-3.5 w-3.5 text-white" />
                </div>
            );
        case "active":
            return (
                <div className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-[hsl(var(--chart-1))] bg-[hsl(var(--chart-1))]/10" aria-label="In progress">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-[hsl(var(--chart-1))]" />
                </div>
            );
        case "error":
            return (
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-destructive" aria-label="Error">
                    <X className="h-3.5 w-3.5 text-white" />
                </div>
            );
        case "skipped":
            return (
                <div className="flex h-6 w-6 items-center justify-center rounded-full bg-muted" aria-label="Skipped">
                    <Minus className="text-muted-foreground h-3.5 w-3.5" />
                </div>
            );
        case "pending":
        default:
            return (
                <div className="bg-muted flex h-6 w-6 items-center justify-center rounded-full" aria-label="Pending">
                    <div className="bg-muted-foreground/40 h-2 w-2 rounded-full" />
                </div>
            );
    }
};

export const TriageStepper: React.FC<TriageStepperProps> = ({ currentStage, emergency, error, detail }) => {
    const totalSteps = STEPS.length;

    return (
        <div className="px-4 py-3">
            {/* Step indicators */}
            <div className="flex items-center justify-between">
                {STEPS.map((step, index) => {
                    const status = getStepStatus(index, currentStage, totalSteps, emergency, error);
                    const isSkipped = status === "skipped";

                    return (
                        <React.Fragment key={step}>
                            <div className="flex flex-col items-center gap-1">
                                <StepIcon status={status} />
                                <span
                                    className={`text-[10px] font-medium ${
                                        status === "active"
                                            ? "text-[hsl(var(--chart-1))]"
                                            : status === "complete"
                                              ? "text-[hsl(var(--chart-2))]"
                                              : status === "error"
                                                ? "text-destructive"
                                                : "text-muted-foreground"
                                    } ${isSkipped ? "line-through" : ""}`}
                                >
                                    {step}
                                </span>
                            </div>
                            {index < STEPS.length - 1 && (
                                <div
                                    className={`mx-1 h-px flex-1 ${
                                        getStepStatus(index, currentStage, totalSteps, emergency, error) === "complete"
                                            ? "bg-[hsl(var(--chart-2))]"
                                            : "bg-border"
                                    }`}
                                />
                            )}
                        </React.Fragment>
                    );
                })}
            </div>

            {/* Detail text */}
            {detail && (
                <p className="text-muted-foreground mt-2 text-center text-xs">{detail}</p>
            )}
        </div>
    );
};
