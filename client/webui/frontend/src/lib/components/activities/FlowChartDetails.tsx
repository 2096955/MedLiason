import { useMemo, useState, useEffect, type JSX } from "react";
import { Download, Clock, Zap } from "lucide-react";

import { Badge, Button } from "@/lib/components/ui";
import { useChatContext, useConfigContext } from "@/lib/hooks";
import { getErrorMessage } from "@/lib/utils/api";

import type { MessageFE, TextPart, VisualizedTask } from "@/lib/types";

import { LoadingMessageRow } from "../chat";
import { downloadBlob } from "@/lib/utils";
import { api } from "@/lib/api";

interface TaskPerformance {
    taskId: string;
    status: string;
    durationMs: number | null;
    tokenUsage: {
        totalInputTokens: number | null;
        totalOutputTokens: number | null;
        totalCachedInputTokens: number | null;
    };
}

const getStatusBadge = (status: string, type: "info" | "error" | "success") => {
    return (
        <Badge type={type} className={`rounded-full border-none`}>
            <span className="text-xs font-semibold" title={status}>
                {status}
            </span>
        </Badge>
    );
};

const getTaskStatus = (task: VisualizedTask, loadingMessage: MessageFE | undefined): string | JSX.Element => {
    // Prioritize the specific status text from the visualizer if available
    if (task.currentStatusText) {
        return (
            <div title={task.currentStatusText}>
                <LoadingMessageRow statusText={task.currentStatusText} />
            </div>
        );
    }

    const loadingMessageText = loadingMessage?.parts
        ?.filter(p => p.kind === "text")
        .map(p => (p as TextPart).text)
        .join("");

    // Fallback to the overall task status
    switch (task.status) {
        case "submitted":
        case "working":
            return (
                <div title={loadingMessageText || task.status}>
                    <LoadingMessageRow statusText={loadingMessageText || task.status} />
                </div>
            );
        case "input-required":
            return getStatusBadge("Input Required", "info");
        case "completed":
            return getStatusBadge("Completed", "success");
        case "canceled":
            return getStatusBadge("Canceled", "info");
        case "failed":
            return getStatusBadge("Failed", "error");
        default:
            return getStatusBadge("Unknown", "info");
    }
};

export const FlowChartDetails: React.FC<{ task: VisualizedTask }> = ({ task }) => {
    const { messages, addNotification, displayError } = useChatContext();
    const { configFeatureEnablement } = useConfigContext();
    const taskLoggingEnabled = configFeatureEnablement?.taskLogging ?? false;
    const [performance, setPerformance] = useState<TaskPerformance | null>(null);

    const taskStatus = useMemo(() => {
        const loadingMessage = messages.find(message => message.isStatusBubble);

        return task ? getTaskStatus(task, loadingMessage) : null;
    }, [messages, task]);

    // Fetch performance data when task completes
    useEffect(() => {
        if (task?.taskId && task.status === "completed") {
            api.webui
                .get(`/api/v1/tasks/${task.taskId}/performance`)
                .then((data: TaskPerformance) => setPerformance(data))
                .catch(() => setPerformance(null));
        }
    }, [task?.taskId, task?.status]);

    const handleDownloadStim = async () => {
        try {
            const response = await api.webui.get(`/api/v1/tasks/${task.taskId}`, { fullResponse: true });
            if (!response.ok) {
                throw new Error(`Failed to download task log: ${response.statusText}`);
            }
            const blob = await response.blob();
            downloadBlob(blob, `${task.taskId}.stim`);
            addNotification("Task log downloaded", "success");
        } catch (error) {
            displayError({ title: "Failed to Download Task Log", error: getErrorMessage(error, "An unknown error occurred while downloading the task log.") });
        }
    };

    const formatDuration = (ms: number) => {
        if (ms < 1000) return `${ms}ms`;
        if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
        return `${(ms / 60000).toFixed(1)}m`;
    };

    return task ? (
        <div className="border-b p-4">
            <div className="grid grid-cols-[auto_1fr_auto] grid-rows-[32px_32px] items-center gap-x-8">
                <div className="text-muted-foreground">User</div>
                <div className="truncate" title={task.initialRequestText}>
                    {task.initialRequestText}
                </div>
                {/* Empty cell for alignment */}
                <div />

                <div className="text-muted-foreground">Status</div>
                <div className="truncate">{taskStatus}</div>

                <div>
                    {taskLoggingEnabled && (
                        <Button variant="ghost" size="icon" onClick={handleDownloadStim} tooltip="Download Task Log (.stim)" disabled={!task.taskId}>
                            <Download />
                        </Button>
                    )}
                </div>
            </div>

            {/* Performance metrics */}
            {performance && (performance.durationMs || performance.tokenUsage.totalInputTokens) && (
                <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-3 border-t pt-3 text-xs">
                    {performance.durationMs != null && (
                        <span className="flex items-center gap-1" title="Task duration">
                            <Clock className="h-3 w-3" />
                            {formatDuration(performance.durationMs)}
                        </span>
                    )}
                    {performance.tokenUsage.totalInputTokens != null && (
                        <span className="flex items-center gap-1" title="Token usage (input / output)">
                            <Zap className="h-3 w-3" />
                            {performance.tokenUsage.totalInputTokens.toLocaleString()} in / {(performance.tokenUsage.totalOutputTokens ?? 0).toLocaleString()} out
                        </span>
                    )}
                    {performance.tokenUsage.totalCachedInputTokens != null && performance.tokenUsage.totalCachedInputTokens > 0 && (
                        <span title="Cached input tokens">({performance.tokenUsage.totalCachedInputTokens.toLocaleString()} cached)</span>
                    )}
                </div>
            )}
        </div>
    ) : null;
};
