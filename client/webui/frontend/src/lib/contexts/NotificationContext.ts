import { createContext, useContext } from "react";

import type { Notification } from "@/lib/types";

export interface NotificationContextValue {
    notifications: Notification[];
    configCollectFeedback: boolean;
    submittedFeedback: Record<string, { type: "up" | "down"; text: string }>;
    addNotification: (message: string, type?: "success" | "info" | "warning") => void;
    handleFeedbackSubmit: (taskId: string, feedbackType: "up" | "down", feedbackText: string) => Promise<void>;
    displayError: ({ title, error }: { title: string; error: string }) => void;
}

export const NotificationContext = createContext<NotificationContextValue | undefined>(undefined);

export const useNotificationContext = (): NotificationContextValue => {
    const context = useContext(NotificationContext);
    if (context === undefined) {
        throw new Error("useNotificationContext must be used within a ChatProvider");
    }
    return context;
};
