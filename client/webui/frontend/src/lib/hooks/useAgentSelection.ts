import { useCallback } from "react";
import { useChatContext } from "./useChatContext";

export const useAgentSelection = () => {
    const { agents, sessionId, setMessages, setSelectedAgentName, handleNewSession } = useChatContext();

    const handleAgentSelection = useCallback(
        (agentName: string, startNewChat = false) => {
            if (agentName) {
                if (startNewChat) {
                    handleNewSession();
                }

                // Always update the desired agent — the isAgentAvailable
                // guard on the submit button prevents sending to an agent
                // that hasn't registered yet.
                setSelectedAgentName(agentName);

                const selectedAgent = agents.find(agent => agent.name === agentName);
                if (selectedAgent) {
                    const displayedText = `Hi! I'm the ${selectedAgent.displayName}. How can I help?`;
                    setMessages(prev => [
                        ...prev,
                        {
                            parts: [{ kind: "text", text: displayedText }],
                            isUser: false,
                            isComplete: true,
                            role: "agent",
                            metadata: {
                                sessionId: sessionId || "",
                                lastProcessedEventSequence: 0,
                            },
                        },
                    ]);
                }
            }
        },
        [agents, sessionId, setMessages, setSelectedAgentName, handleNewSession]
    );

    return { handleAgentSelection };
};
