import React, { useState } from "react";
import { Ambulance, Building, UserCheck, Stethoscope, Pill, Home, ChevronDown, ChevronRight, AlertTriangle, Clock } from "lucide-react";
import { Badge } from "@/lib/components/ui";

interface NBAProps {
    diagnosis: string;
    route: string;
    urgency: string;
    color: string;
    specialist_type?: string;
    reasoning: string;
    self_care_instructions?: string;
    escalation_criteria?: string;
    follow_up_timeframe?: string;
    disclaimer: string;
}

/** Map route string to a lucide icon component. */
function getRouteIcon(route: string) {
    const lower = route.toLowerCase();
    if (lower.includes("emergency") || lower.includes("ambulance") || lower.includes("999") || lower.includes("911")) {
        return Ambulance;
    }
    if (lower.includes("hospital") || lower.includes("a&e") || lower.includes("er")) {
        return Building;
    }
    if (lower.includes("specialist")) {
        return UserCheck;
    }
    if (lower.includes("gp") || lower.includes("primary") || lower.includes("doctor")) {
        return Stethoscope;
    }
    if (lower.includes("pharma")) {
        return Pill;
    }
    if (lower.includes("self") || lower.includes("home")) {
        return Home;
    }
    // Default
    return Stethoscope;
}

/** Map urgency to icon + accessible label. */
function getUrgencyIcon(urgency: string) {
    const lower = urgency.toLowerCase();
    if (lower.includes("immediate") || lower.includes("emergency") || lower.includes("critical")) {
        return { icon: Ambulance, label: "Immediate attention required" };
    }
    if (lower.includes("urgent") || lower.includes("soon")) {
        return { icon: AlertTriangle, label: "Urgent" };
    }
    return { icon: Clock, label: "Routine" };
}

/** Map a color name/hex to Tailwind classes for the urgency badge. */
function urgencyBadgeClasses(color: string): string {
    const lower = color.toLowerCase();
    if (lower === "red" || lower === "#ff0000" || lower.includes("red")) {
        return "border-destructive bg-destructive/10 text-destructive";
    }
    if (lower === "orange" || lower.includes("orange") || lower === "amber" || lower.includes("amber")) {
        return "border-[hsl(var(--chart-4))] bg-[hsl(var(--chart-4))]/10 text-[hsl(var(--chart-4))]";
    }
    if (lower === "yellow" || lower.includes("yellow")) {
        return "border-[hsl(var(--chart-4))] bg-[hsl(var(--chart-4))]/10 text-[hsl(var(--chart-4))]";
    }
    if (lower === "green" || lower.includes("green")) {
        return "border-[hsl(var(--chart-2))] bg-[hsl(var(--chart-2))]/10 text-[hsl(var(--chart-2))]";
    }
    if (lower === "blue" || lower.includes("blue")) {
        return "border-[hsl(var(--chart-1))] bg-[hsl(var(--chart-1))]/10 text-[hsl(var(--chart-1))]";
    }
    // Default neutral
    return "border-border bg-muted text-muted-foreground";
}

export const NBAActionCard: React.FC<NBAProps> = ({
    diagnosis,
    route,
    urgency,
    color,
    specialist_type,
    reasoning,
    self_care_instructions,
    escalation_criteria,
    follow_up_timeframe,
    disclaimer,
}) => {
    const [selfCareExpanded, setSelfCareExpanded] = useState(false);

    const RouteIcon = getRouteIcon(route);
    const { icon: UrgencyIcon, label: urgencyLabel } = getUrgencyIcon(urgency);
    const badgeClasses = urgencyBadgeClasses(color);

    return (
        <div className="rounded-md border-2 border-border bg-muted/20 p-4">
            {/* Header with icon and diagnosis */}
            <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-muted">
                    <RouteIcon className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                    <h4 className="text-sm font-semibold">{diagnosis}</h4>
                    <p className="text-muted-foreground mt-0.5 text-xs">{route}</p>
                </div>
            </div>

            {/* Urgency badge — uses color AND icon AND text for accessibility */}
            <div className="mt-3 flex items-center gap-2">
                <Badge variant="outline" className={`gap-1 px-2 py-0.5 text-xs ${badgeClasses}`}>
                    <UrgencyIcon className="h-3 w-3" />
                    <span>{urgency}</span>
                </Badge>
                <span className="sr-only">{urgencyLabel}</span>
                {specialist_type && (
                    <Badge variant="outline" className="text-xs px-2 py-0.5">
                        {specialist_type}
                    </Badge>
                )}
            </div>

            {/* Reasoning */}
            <div className="mt-3">
                <p className="text-xs leading-relaxed">{reasoning}</p>
            </div>

            {/* Self-care instructions (expandable) */}
            {self_care_instructions && (
                <div className="mt-3 rounded border border-border">
                    <button
                        type="button"
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium hover:bg-muted/50"
                        onClick={() => setSelfCareExpanded(prev => !prev)}
                    >
                        {selfCareExpanded ? (
                            <ChevronDown className="h-3 w-3 flex-shrink-0" />
                        ) : (
                            <ChevronRight className="h-3 w-3 flex-shrink-0" />
                        )}
                        Self-Care Instructions
                    </button>
                    {selfCareExpanded && (
                        <div className="border-t border-border bg-muted/20 px-3 py-2">
                            <p className="whitespace-pre-line text-xs leading-relaxed">{self_care_instructions}</p>
                        </div>
                    )}
                </div>
            )}

            {/* Escalation criteria */}
            {escalation_criteria && (
                <div className="mt-3">
                    <p className="text-muted-foreground text-[10px] font-semibold uppercase tracking-wider">Escalation Criteria</p>
                    <p className="mt-1 text-xs leading-relaxed">{escalation_criteria}</p>
                </div>
            )}

            {/* Follow-up timeframe */}
            {follow_up_timeframe && (
                <div className="mt-3 flex items-center gap-1.5">
                    <Clock className="text-muted-foreground h-3 w-3 flex-shrink-0" />
                    <span className="text-xs">
                        <span className="text-muted-foreground">Follow-up:</span>{" "}
                        <span className="font-medium">{follow_up_timeframe}</span>
                    </span>
                </div>
            )}

            {/* Disclaimer */}
            {disclaimer && (
                <p className="text-muted-foreground mt-4 border-t border-border pt-3 text-[10px] leading-relaxed">
                    {disclaimer}
                </p>
            )}
        </div>
    );
};
