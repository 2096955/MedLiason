/**
 * Centralized selector constants derived from actual component source.
 * Every selector is traced back to a data-testid or aria-label in the codebase.
 */
export const SEL = {
    // Chat input area (MentionContentEditable.tsx:365, chat-input.tsx:12)
    chatInput: '[data-testid="chat-input"]',
    sendButton: '[data-testid="sendMessage"]',
    cancelButton: '[data-testid="cancel"]',

    // Session management (ChatSessionDialog.tsx:14)
    startNewChat: '[data-testid="startNewChat"]',

    // Model / Mode selectors (ChatInputArea.tsx:983, :995)
    modelSelect: '[aria-label="Select AI model"]',
    modeSelect: '[aria-label="Select mode"]',
    // Radix Select items (role="option" rendered by @radix-ui/react-select)
    selectItem: '[role="option"]',

    // Research protocol stepper (ResearchProtocolStepper.tsx)
    protocolToggle: '[aria-label="Toggle research protocol steps"]',

    // Side panel (ChatSidePanel.tsx:177, :224)
    expandPanel: '[data-testid="expandPanel"]',
    collapsePanel: '[data-testid="collapsePanel"]',

    // Workflow (ViewWorkflowButton.tsx:12)
    viewActivity: '[data-testid="viewActivity"]',

    // Knowledge Graph (GraphCanvas.tsx:475, :518)
    kgNode: '[data-testid^="graph-node-"]',
    kgDegreeBadge: (nodeId: string) => `[data-testid="degree-badge-${nodeId}"]`,

    // Message banner (MessageBanner.tsx:37)
    messageBanner: '[data-testid="messageBanner"]',

    // Sessions panel (SessionSidePanel.tsx:19)
    showSessionsPanel: '[data-testid="showSessionsPanel"]',
} as const;
