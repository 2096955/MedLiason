import React from "react";
import { CircleAlert } from "lucide-react";

interface TriageErrorStateProps {
    error: string;
}

export const TriageErrorState: React.FC<TriageErrorStateProps> = ({ error }) => {
    return (
        <div className="mx-4 mt-3 flex items-start gap-3 rounded-md border border-destructive/50 bg-destructive/10 p-4" role="alert">
            <CircleAlert className="mt-0.5 h-5 w-5 flex-shrink-0 text-destructive" />
            <div className="flex-1">
                <p className="text-sm font-medium text-destructive">Triage Error</p>
                <p className="mt-1 text-sm text-destructive/80">{error}</p>
                <p className="text-muted-foreground mt-2 text-xs">
                    The triage pipeline encountered an error. Please try again.
                </p>
            </div>
        </div>
    );
};
