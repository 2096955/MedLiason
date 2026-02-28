import React from "react";
import { Badge } from "@/lib/components/ui";

interface SpecialistVerdictCardProps {
    specialist: string;
    diagnosis: string;
    confidence: number;
    thinking: string;
    tier: "tier1" | "tier2";
}

/** Return a Tailwind colour class based on confidence thresholds. */
function confidenceColor(confidence: number): string {
    if (confidence > 60) return "bg-[hsl(var(--chart-2))]"; // green
    if (confidence >= 30) return "bg-[hsl(var(--chart-4))]"; // yellow
    return "bg-destructive"; // red
}

/** Return a text colour class based on confidence thresholds. */
function confidenceTextColor(confidence: number): string {
    if (confidence > 60) return "text-[hsl(var(--chart-2))]";
    if (confidence >= 30) return "text-[hsl(var(--chart-4))]";
    return "text-destructive";
}

function isInsufficientInfo(diagnosis: string): boolean {
    const lower = diagnosis.toLowerCase();
    return lower.includes("insufficient") || lower.includes("unable to determine") || lower.includes("not enough");
}

export const SpecialistVerdictCard: React.FC<SpecialistVerdictCardProps> = ({
    specialist,
    diagnosis,
    confidence,
    thinking,
    tier,
}) => {
    const insufficient = isInsufficientInfo(diagnosis);

    return (
        <div className={`rounded-md border border-border p-3 ${insufficient ? "opacity-60" : ""}`}>
            {/* Header: specialist name + tier badge */}
            <div className="flex items-center gap-2">
                <span className={`text-sm font-medium ${insufficient ? "text-muted-foreground" : ""}`}>
                    {specialist}
                </span>
                <Badge
                    variant="outline"
                    className={`text-[10px] px-1.5 py-0 ${
                        tier === "tier1"
                            ? "border-[hsl(var(--chart-1))] text-[hsl(var(--chart-1))]"
                            : "border-[hsl(var(--chart-5))] text-[hsl(var(--chart-5))]"
                    }`}
                >
                    {tier === "tier1" ? "Tier 1" : "Tier 2"}
                </Badge>
            </div>

            {/* Diagnosis */}
            <p className={`mt-1.5 text-sm ${insufficient ? "text-muted-foreground italic" : "font-medium"}`}>
                {diagnosis}
            </p>

            {/* Confidence bar */}
            <div className="mt-2 flex items-center gap-2">
                <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                        className={`absolute left-0 top-0 h-full rounded-full transition-all ${confidenceColor(confidence)}`}
                        style={{ width: `${Math.min(100, Math.max(0, confidence))}%` }}
                    />
                </div>
                <span className={`w-8 text-right text-[10px] font-medium tabular-nums ${confidenceTextColor(confidence)}`}>
                    {Math.round(confidence)}%
                </span>
            </div>

            {/* Thinking */}
            {thinking && (
                <p className="text-muted-foreground mt-2 text-xs leading-relaxed">{thinking}</p>
            )}
        </div>
    );
};
