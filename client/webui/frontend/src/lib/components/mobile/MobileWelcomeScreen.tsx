import { Search, Activity, Stethoscope, BookOpen, Pill, Dna, FlaskConical, FileSearch } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { useChatContext } from "@/lib/hooks";
import { useModelSelection } from "@/lib/hooks/useModelSelection";
import { cn } from "@/lib/utils";

interface ModeCard {
    mode: "research" | "triage";
    title: string;
    subtitle: string;
    icon: LucideIcon;
    color: string;
    bgColor: string;
}

const MODE_CARDS: ModeCard[] = [
    {
        mode: "research",
        title: "Deep Research",
        subtitle: "Evidence-based answers from PubMed, clinical trials, FDA, and 10+ medical databases",
        icon: Search,
        color: "text-blue-600 dark:text-blue-400",
        bgColor: "bg-blue-50 dark:bg-blue-950/40",
    },
    {
        mode: "triage",
        title: "Medical Triage",
        subtitle: "Symptom assessment with specialist panel consultation and care routing",
        icon: Activity,
        color: "text-emerald-600 dark:text-emerald-400",
        bgColor: "bg-emerald-50 dark:bg-emerald-950/40",
    },
];

interface QuickQuery {
    text: string;
    icon: LucideIcon;
    mode: "research" | "triage";
}

const QUICK_QUERIES: QuickQuery[] = [
    { text: "Latest Type 2 diabetes treatment guidelines", icon: BookOpen, mode: "research" },
    { text: "Drug interactions: warfarin + antibiotics", icon: Pill, mode: "research" },
    { text: "Genomic biomarkers for immunotherapy in NSCLC", icon: Dna, mode: "research" },
    { text: "I've had persistent headaches and dizziness for 3 days", icon: Stethoscope, mode: "triage" },
    { text: "Compare SSRIs vs SNRIs for major depression", icon: FlaskConical, mode: "research" },
    { text: "Recent Alzheimer's disease clinical trials", icon: FileSearch, mode: "research" },
];

export function MobileWelcomeScreen() {
    const { startNewChatWithPrompt } = useChatContext();
    const { selectedMode, handleModeChange } = useModelSelection();

    const handleModeSelect = (mode: "research" | "triage") => {
        handleModeChange(mode);
    };

    const handleQueryClick = (query: QuickQuery) => {
        if (query.mode !== selectedMode) {
            handleModeChange(query.mode);
        }
        startNewChatWithPrompt({
            promptText: query.text,
            groupId: "sample-query",
            groupName: "Sample Query",
        });
    };

    return (
        <div className="flex h-full flex-col overflow-y-auto px-4 pb-4 pt-6">
            <div className="mb-6 text-center">
                <h1 className="text-foreground mb-1 text-xl font-bold">MedExpert</h1>
                <p className="text-muted-foreground text-sm">AI-powered medical research & triage</p>
            </div>

            <div className="mb-6 grid grid-cols-2 gap-3">
                {MODE_CARDS.map((card) => {
                    const Icon = card.icon;
                    const isSelected = selectedMode === card.mode;
                    return (
                        <button
                            key={card.mode}
                            type="button"
                            onClick={() => handleModeSelect(card.mode)}
                            className={cn(
                                "flex flex-col items-center gap-2 rounded-xl border-2 p-4 text-center transition-all active:scale-[0.98]",
                                isSelected
                                    ? "border-[var(--color-brand-wMain)] bg-[var(--color-brand-wMain)]/5 shadow-sm"
                                    : "border-border hover:border-muted-foreground/30"
                            )}
                        >
                            <div className={cn("rounded-full p-2.5", card.bgColor)}>
                                <Icon className={cn("h-6 w-6", card.color)} />
                            </div>
                            <div>
                                <div className="text-sm font-semibold text-foreground">{card.title}</div>
                                <div className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
                                    {card.subtitle}
                                </div>
                            </div>
                        </button>
                    );
                })}
            </div>

            <div className="mb-3">
                <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Quick start
                </h2>
            </div>

            <div className="flex flex-col gap-2">
                {QUICK_QUERIES.map((query) => {
                    const Icon = query.icon;
                    return (
                        <button
                            key={query.text}
                            type="button"
                            onClick={() => handleQueryClick(query)}
                            className="flex items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-left transition-colors active:bg-accent"
                        >
                            <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="text-sm text-foreground">{query.text}</span>
                            <span
                                className={cn(
                                    "ml-auto mt-0.5 shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium uppercase",
                                    query.mode === "triage"
                                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400"
                                        : "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400"
                                )}
                            >
                                {query.mode === "triage" ? "Triage" : "Research"}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
