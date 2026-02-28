import React, { createContext, useContext } from "react";

export interface SidePanelContextValue {
    isSidePanelCollapsed: boolean;
    activeSidePanelTab: "files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage";
    taskIdInSidePanel: string | null;
    setIsSidePanelCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
    setActiveSidePanelTab: React.Dispatch<React.SetStateAction<"files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage">>;
    openSidePanelTab: (tab: "files" | "activity" | "rag" | "datasources" | "memory" | "performance" | "triage") => void;
    setTaskIdInSidePanel: React.Dispatch<React.SetStateAction<string | null>>;
}

export const SidePanelContext = createContext<SidePanelContextValue | undefined>(undefined);

export const useSidePanelContext = (): SidePanelContextValue => {
    const context = useContext(SidePanelContext);
    if (context === undefined) {
        throw new Error("useSidePanelContext must be used within a ChatProvider");
    }
    return context;
};
