import { useMemo } from "react";

import { Button, EmptyState, Header } from "@/lib/components";
import { AgentMeshCards } from "@/lib/components/agents";
import { useChatContext } from "@/lib/hooks";
import { isWorkflowAgent } from "@/lib/utils/agentUtils";
import { RefreshCcw } from "lucide-react";

export function AgentMeshPage() {
    const { agents, agentsLoading, agentsError, agentsRefetch } = useChatContext();

    const regularAgents = useMemo(() => {
        return agents.filter(agent => !isWorkflowAgent(agent));
    }, [agents]);

    return (
        <div className="flex h-full w-full flex-col">
            <Header
                title="Agent Configs"
                buttons={[
                    <Button key="refresh" data-testid="refreshAgents" disabled={agentsLoading} variant="ghost" title="Refresh Agents" onClick={() => agentsRefetch()}>
                        <RefreshCcw className="size-4" />
                        Refresh
                    </Button>,
                ]}
            />

            {agentsLoading ? (
                <EmptyState title="Loading..." variant="loading" />
            ) : agentsError ? (
                <EmptyState variant="error" title="Error loading data" subtitle={agentsError} />
            ) : (
                <div className="relative min-h-0 flex-1 overflow-hidden"><AgentMeshCards agents={regularAgents} /></div>
            )}
        </div>
    );
}
