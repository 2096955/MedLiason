import React, { createContext, type FormEvent } from "react";

import type { AgentCardInfo, ArtifactInfo, BackgroundTaskNotification, BackgroundTaskState, FileAttachment, MessageFE, Notification, PipelineErrorData, Session, RAGSearchResult } from "@/lib/types";

/** Pending prompt data for starting a new chat with a prompt template */
export interface PendingPromptData {
    promptText: string;
    groupId: string;
    groupName: string;
}

/** Triage progress data delivered via SSE events */
export interface TriageProgressData {
    type: "triage_progress";
    stage: number;
    stage_name: string;
    total_stages: number;
    detail: string;
    specialist_verdicts?: Array<{
        specialist: string;
        diagnosis: string;
        confidence: number;
        thinking: string;
        tier: "tier1" | "tier2";
    }>;
    consensus?: {
        consensus_diagnosis: string;
        mean_confidence: number;
        supporting_specialists: Array<{ specialist: string; confidence: number }>;
        dissenting_specialists: Array<{
            specialist: string;
            alternative_diagnosis: string;
            confidence: number;
        }>;
    };
    evaluation?: {
        eval_confidence: number;
        explanation: string;
        flag_for_review: boolean;
        flag_reason?: string;
        confirmed_emergency?: boolean;
    };
    nba?: {
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
    };
    emergency_override: boolean;
    error?: string;
}

export interface ChatState {
    configCollectFeedback: boolean;
    sessionId: string;
    sessionName: string | null;
    messages: MessageFE[];
    isResponding: boolean;
    currentTaskId: string | null;
    selectedAgentName: string;
    notifications: Notification[];
    isCancelling: boolean;
    latestStatusText: React.RefObject<string | null>;
    isLoadingSession: boolean;
    // Agents
    agents: AgentCardInfo[];
    agentsError: string | null;
    agentsLoading: boolean;
    agentsRefetch: () => Promise<void>;
    agentNameDisplayNameMap: Record<string, string>;
    // Chat Side Panel State
    artifacts: ArtifactInfo[];
    artifactsLoading: boolean;
    artifactsRefetch: () => Promise<void>;
    setArtifacts: React.Dispatch<React.SetStateAction<ArtifactInfo[]>>;
    taskIdInSidePanel: string | null;
    // RAG State
    ragData: RAGSearchResult[];
    ragEnabled: boolean;
    highlightedSourceId: string | null;
    // Triage State
    triageProgress: TriageProgressData | null;
    // Pipeline Error State
    pipelineErrors: PipelineErrorData | null;
    // Side Panel Control State
    isSidePanelCollapsed: boolean;
    activeSidePanelTab: "files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage";
    // Delete Modal State
    isDeleteModalOpen: boolean;
    artifactToDelete: ArtifactInfo | null;
    sessionToDelete: Session | null;
    // Artifact Edit Mode State
    isArtifactEditMode: boolean;
    selectedArtifactFilenames: Set<string>;
    isBatchDeleteModalOpen: boolean;
    // Versioning Preview State
    previewArtifact: ArtifactInfo | null;
    previewedArtifactAvailableVersions: number[] | null;
    currentPreviewedVersionNumber: number | null;
    previewFileContent: FileAttachment | null;
    submittedFeedback: Record<string, { type: "up" | "down"; text: string }>;
    // Pending prompt for starting new chat
    pendingPrompt: PendingPromptData | null;
    // Background Task Monitoring State
    backgroundTasks: BackgroundTaskState[];
    backgroundNotifications: BackgroundTaskNotification[];
}

export interface ChatActions {
    setSessionId: React.Dispatch<React.SetStateAction<string>>;
    setSessionName: React.Dispatch<React.SetStateAction<string | null>>;
    setMessages: React.Dispatch<React.SetStateAction<MessageFE[]>>;
    setTaskIdInSidePanel: React.Dispatch<React.SetStateAction<string | null>>;
    handleNewSession: (preserveProjectContext?: boolean) => Promise<void>;
    /** Start a new chat session with a prompt template pre-filled */
    startNewChatWithPrompt: (promptData: PendingPromptData) => void;
    /** Clear the pending prompt (called after it's been applied) */
    clearPendingPrompt: () => void;
    handleSwitchSession: (sessionId: string) => Promise<void>;
    handleSubmit: (event: FormEvent, files?: File[] | null, message?: string | null, overrideSessionId?: string | null, displayHtml?: string | null, contextQuote?: string | null, contextQuoteSourceId?: string | null) => Promise<void>;
    handleCancel: () => void;
    addNotification: (message: string, type?: "success" | "info" | "warning") => void;
    setSelectedAgentName: React.Dispatch<React.SetStateAction<string>>;
    uploadArtifactFile: (file: File, overrideSessionId?: string, description?: string, silent?: boolean) => Promise<{ uri: string; sessionId: string } | { error: string } | null>;
    /** Triage Actions */
    setTriageProgress: (progress: TriageProgressData | null) => void;
    /** Pipeline Error Actions */
    setPipelineErrors: (errors: PipelineErrorData | null) => void;
    /** Side Panel Control Actions */
    setIsSidePanelCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
    setActiveSidePanelTab: React.Dispatch<React.SetStateAction<"files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage">>;
    openSidePanelTab: (tab: "files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage") => void;

    openDeleteModal: (artifact: ArtifactInfo) => void;
    closeDeleteModal: () => void;
    confirmDelete: () => Promise<void>;
    openSessionDeleteModal: (session: Session) => void;
    closeSessionDeleteModal: () => void;
    confirmSessionDelete: () => Promise<void>;

    setIsArtifactEditMode: React.Dispatch<React.SetStateAction<boolean>>;
    setSelectedArtifactFilenames: React.Dispatch<React.SetStateAction<Set<string>>>;
    handleDeleteSelectedArtifacts: () => void;
    confirmBatchDeleteArtifacts: () => Promise<void>;
    setIsBatchDeleteModalOpen: React.Dispatch<React.SetStateAction<boolean>>;

    setPreviewArtifact: (artifact: ArtifactInfo | null) => void;
    openArtifactForPreview: (artifactFilename: string) => Promise<FileAttachment | null>;
    navigateArtifactVersion: (artifactFilename: string, targetVersion: number) => Promise<FileAttachment | null>;

    /** Artifact Display and Cache Management */
    markArtifactAsDisplayed: (filename: string, displayed: boolean) => void;
    downloadAndResolveArtifact: (filename: string) => Promise<FileAttachment | null>;

    /* Session Management Actions */
    updateSessionName: (sessionId: string, newName: string) => Promise<void>;
    deleteSession: (sessionId: string) => Promise<void>;
    handleFeedbackSubmit: (taskId: string, feedbackType: "up" | "down", feedbackText: string) => Promise<void>;

    displayError: ({ title, error }: { title: string; error: string }) => void;
    setHighlightedSourceId: React.Dispatch<React.SetStateAction<string | null>>;

    /** Background Task Monitoring Actions */
    isTaskRunningInBackground: (taskId: string) => boolean;
}

export type ChatContextValue = ChatState & ChatActions;

export const ChatContext = createContext<ChatContextValue | undefined>(undefined);
