/**
 * Network monitoring — shows overlay when offline.
 * Uses @capacitor/network for native connectivity detection.
 */
import { Capacitor } from "@capacitor/core";
import { Network } from "@capacitor/network";

const OVERLAY_ID = "medexpert-offline-overlay";

export function initNetworkMonitor(): void {
    if (!Capacitor.isNativePlatform()) return;

    Network.addListener("networkStatusChange", ({ connected }) => {
        if (!connected) {
            showOfflineOverlay();
        } else {
            hideOfflineOverlay();
        }
    });
}

function showOfflineOverlay(): void {
    if (document.getElementById(OVERLAY_ID)) return;

    const div = document.createElement("div");
    div.id = OVERLAY_ID;
    div.innerHTML = `
        <div style="position:fixed;inset:0;background:rgba(0,0,0,0.85);
            display:flex;align-items:center;justify-content:center;z-index:99999;
            color:white;font-family:system-ui;text-align:center;padding:2rem;">
            <div>
                <h2 style="font-size:1.5rem;margin-bottom:0.5rem;">No Internet Connection</h2>
                <p style="opacity:0.8;">MedExpert requires an internet connection for medical research queries.</p>
            </div>
        </div>`;
    document.body.appendChild(div);
}

function hideOfflineOverlay(): void {
    document.getElementById(OVERLAY_ID)?.remove();
}
