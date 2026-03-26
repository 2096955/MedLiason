import React, { useState, useCallback } from "react";
import { Send, Loader2 } from "lucide-react";

import { Button } from "@/lib/components/ui";

interface NLQResponse {
    generated_cypher?: string;
    results?: unknown[];
    total_results?: number;
    error?: string;
}

interface GraphQueryBarProps {
    onResultNodes?: (nodeIds: string[]) => void;
}

const GraphQueryBar: React.FC<GraphQueryBarProps> = ({ onResultNodes }) => {
    const [question, setQuestion] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [response, setResponse] = useState<NLQResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    const handleSubmit = useCallback(
        async (e: React.FormEvent) => {
            e.preventDefault();
            const trimmed = question.trim();
            if (!trimmed || isLoading) return;

            setIsLoading(true);
            setError(null);
            setResponse(null);

            try {
                const res = await fetch("/api/v1/graph/nlq", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "include",
                    body: JSON.stringify({ question: trimmed }),
                });

                if (!res.ok) {
                    throw new Error(`Query failed (${res.status})`);
                }

                const data: NLQResponse = await res.json();
                setResponse(data);

                // Highlight result nodes in graph
                if (data.results && Array.isArray(data.results) && onResultNodes) {
                    const nodeIds = data.results
                        .filter((n): n is Record<string, unknown> => typeof n === "object" && n !== null)
                        .map((n) => String(n.id))
                        .filter(Boolean);
                    onResultNodes(nodeIds);
                }
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to execute query");
            } finally {
                setIsLoading(false);
            }
        },
        [question, isLoading, onResultNodes]
    );

    return (
        <div className="border-t border-border bg-background px-4 py-3">
            <form onSubmit={handleSubmit} className="flex items-center gap-2">
                <input
                    type="text"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="Ask about the knowledge graph..."
                    className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                    disabled={isLoading}
                />
                <Button type="submit" size="sm" disabled={isLoading || !question.trim()} className="h-9 w-9 p-0">
                    {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                </Button>
            </form>

            {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

            {response?.generated_cypher && (
                <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">Generated Cypher</summary>
                    <pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-xs text-foreground">{response.generated_cypher}</pre>
                </details>
            )}

            {response?.error && <p className="mt-2 text-xs text-destructive">{response.error}</p>}
        </div>
    );
};

export default GraphQueryBar;
