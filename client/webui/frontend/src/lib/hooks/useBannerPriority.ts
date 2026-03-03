import { useMemo } from "react";

/**
 * Banner types in priority order (highest first).
 * Only one banner renders at a time to prevent stacking.
 */
export type BannerType = "offline" | "update" | "install" | "triage" | null;

interface BannerPriorityInput {
    isOffline: boolean;
    needsUpdate: boolean;
    canInstall: boolean;
    triageActive: boolean;
}

/**
 * Returns which banner (if any) should be visible.
 * Priority: offline > update > install > triage.
 * Only one banner at a time — prevents z-index stacking chaos (F5).
 */
export function useBannerPriority({
    isOffline,
    needsUpdate,
    canInstall,
    triageActive,
}: BannerPriorityInput): BannerType {
    return useMemo(() => {
        if (isOffline) return "offline";
        if (needsUpdate) return "update";
        if (canInstall) return "install";
        if (triageActive) return "triage";
        return null;
    }, [isOffline, needsUpdate, canInstall, triageActive]);
}
