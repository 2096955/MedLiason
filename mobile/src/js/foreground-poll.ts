/**
 * Foreground-resume polling — checks research completion when app returns
 * from background. iOS suspends WKWebView in background, dropping SSE.
 * This is the Tier 1 interim solution (F6).
 */
import { Capacitor } from "@capacitor/core";
import { App } from "@capacitor/app";

let activeTaskId: string | null = null;

export function initForegroundPoll(): void {
    if (!Capacitor.isNativePlatform()) return;

    // Track active research task via window message from React app
    window.addEventListener("medexpert-task-active", ((e: CustomEvent<{ taskId: string }>) => {
        activeTaskId = e.detail.taskId;
    }) as EventListener);

    window.addEventListener("medexpert-task-complete", () => {
        activeTaskId = null;
    });

    // When app returns to foreground, check if research completed
    App.addListener("appStateChange", async ({ isActive }) => {
        if (isActive && activeTaskId) {
            try {
                const resp = await fetch(`/api/v1/tasks/${activeTaskId}/status`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (!data.is_running) {
                        // Notify the React app
                        window.dispatchEvent(
                            new CustomEvent("medexpert-task-resumed", {
                                detail: { taskId: activeTaskId, status: data.status },
                            })
                        );
                        activeTaskId = null;
                    }
                }
            } catch (e) {
                console.warn("[ForegroundPoll] Failed to check task status:", e);
            }
        }
    });
}
