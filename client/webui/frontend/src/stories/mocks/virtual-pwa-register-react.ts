/**
 * Mock for virtual:pwa-register/react
 *
 * The vite-plugin-pwa provides this as a Vite virtual module at build time.
 * In the jsdom test environment (vitest unit), the virtual module is not
 * available. This mock provides the same API shape with safe no-op defaults.
 */
import { useState } from "react";

export function useRegisterSW(_options?: {
    onRegisteredSW?: (swUrl: string, registration: ServiceWorkerRegistration | undefined) => void;
    onRegisterError?: (error: Error) => void;
}) {
    const [needRefresh, setNeedRefresh] = useState(false);
    const [offlineReady, setOfflineReady] = useState(false);

    return {
        needRefresh: [needRefresh, setNeedRefresh] as [boolean, (val: boolean) => void],
        offlineReady: [offlineReady, setOfflineReady] as [boolean, (val: boolean) => void],
        updateServiceWorker: async (_reloadPage?: boolean) => {},
    };
}
