import { useRegisterSW } from "virtual:pwa-register/react";
import { useCallback, useEffect, useRef, useState } from "react";

const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000; // Check hourly

export function usePWA() {
    const registrationRef = useRef<ServiceWorkerRegistration>(undefined!);
    const [swReady, setSwReady] = useState(false);

    const {
        needRefresh: [needRefresh, setNeedRefresh],
        offlineReady: [offlineReady, setOfflineReady],
        updateServiceWorker,
    } = useRegisterSW({
        onRegisteredSW(_swUrl, registration) {
            if (registration) {
                registrationRef.current = registration;
                setSwReady(true);
            }
        },
        onRegisterError(error) {
            console.error("[PWA] Service worker registration failed:", error);
        },
    });

    // Manage hourly update check with proper cleanup
    useEffect(() => {
        if (!swReady || !registrationRef.current) return;
        const id = setInterval(() => {
            registrationRef.current?.update();
        }, UPDATE_CHECK_INTERVAL_MS);
        return () => clearInterval(id);
    }, [swReady]);

    const acceptUpdate = useCallback(() => {
        updateServiceWorker(true);
    }, [updateServiceWorker]);

    const dismissUpdate = useCallback(() => {
        setNeedRefresh(false);
    }, [setNeedRefresh]);

    const dismissOfflineReady = useCallback(() => {
        setOfflineReady(false);
    }, [setOfflineReady]);

    return {
        needRefresh,
        offlineReady,
        acceptUpdate,
        dismissUpdate,
        dismissOfflineReady,
    };
}
