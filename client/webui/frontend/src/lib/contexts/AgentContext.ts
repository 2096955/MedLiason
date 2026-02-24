import { createContext, useContext } from "react";

import type { AgentCardInfo } from "@/lib/types";

export interface AgentContextValue {
    agents: AgentCardInfo[];
    agentsError: string | null;
    agentsLoading: boolean;
    agentsRefetch: () => Promise<void>;
    agentNameDisplayNameMap: Record<string, string>;
}

export const AgentContext = createContext<AgentContextValue | undefined>(undefined);

export const useAgentContext = (): AgentContextValue => {
    const context = useContext(AgentContext);
    if (context === undefined) {
        throw new Error("useAgentContext must be used within a ChatProvider");
    }
    return context;
};
