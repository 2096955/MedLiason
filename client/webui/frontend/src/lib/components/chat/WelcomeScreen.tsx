import { MessageSquareText } from "lucide-react";

import { useChatContext } from "@/lib/hooks";
import { CHAT_STYLES } from "@/lib/components/ui";

const SAMPLE_QUERIES = [
    "What are the latest treatment guidelines for Type 2 diabetes?",
    "Compare efficacy and safety of SSRIs vs SNRIs for major depression",
    "What genomic biomarkers predict response to immunotherapy in NSCLC?",
    "Summarize recent clinical trials for Alzheimer's disease treatments",
    "What are the drug interactions between warfarin and common antibiotics?",
];

export function WelcomeScreen() {
    const { startNewChatWithPrompt } = useChatContext();

    const handleQueryClick = (query: string) => {
        startNewChatWithPrompt({
            promptText: query,
            groupId: "sample-query",
            groupName: "Sample Query",
        });
    };

    return (
        <div className="flex h-full flex-col items-center justify-center px-4" style={CHAT_STYLES}>
            <div className="mb-8 text-center">
                <h1 className="text-foreground mb-2 text-2xl font-semibold">What can I help you with?</h1>
                <p className="text-muted-foreground text-sm">Click a question below or type your own</p>
            </div>
            <div className="flex w-full max-w-2xl flex-col gap-3">
                {SAMPLE_QUERIES.map((query) => (
                    <button
                        key={query}
                        type="button"
                        onClick={() => handleQueryClick(query)}
                        className="hover:bg-accent border-border text-foreground flex items-start gap-3 rounded-lg border px-4 py-3 text-left text-sm transition-colors"
                    >
                        <MessageSquareText className="text-muted-foreground mt-0.5 size-4 shrink-0" />
                        <span>{query}</span>
                    </button>
                ))}
            </div>
        </div>
    );
}
