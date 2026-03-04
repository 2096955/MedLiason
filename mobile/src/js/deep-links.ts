/**
 * Deep link handler — routes medexpert:// URLs to the React app's hash router.
 */
import { Capacitor } from "@capacitor/core";
import { App } from "@capacitor/app";

export function initDeepLinks(): void {
    if (!Capacitor.isNativePlatform()) return;

    App.addListener("appUrlOpen", ({ url }) => {
        try {
            const parsed = new URL(url);
            const segments = parsed.pathname.split("/").filter(Boolean);

            // medexpert://session/{id} → /#/chat?session={id}
            if (segments[0] === "session" && segments[1]) {
                window.location.hash = `/chat?session=${segments[1]}`;
            }
        } catch (e) {
            console.warn("[DeepLinks] Failed to parse URL:", url, e);
        }
    });
}
