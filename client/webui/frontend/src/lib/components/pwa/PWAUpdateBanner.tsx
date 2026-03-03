import { RefreshCw, X } from "lucide-react";
import { Button } from "@/lib/components/ui";
import { Z_PWA_UPDATE_BANNER } from "@/lib/constants/zIndex";

interface PWAUpdateBannerProps {
    needRefresh: boolean;
    offlineReady: boolean;
    onAcceptUpdate: () => void;
    onDismissUpdate: () => void;
    onDismissOfflineReady: () => void;
}

export function PWAUpdateBanner({
    needRefresh,
    offlineReady,
    onAcceptUpdate,
    onDismissUpdate,
    onDismissOfflineReady,
}: PWAUpdateBannerProps) {
    if (!needRefresh && !offlineReady) return null;

    return (
        <div
            className="fixed inset-x-0 top-0 flex items-center justify-between bg-[var(--color-brand-wMain)] px-4 py-2 text-white shadow-md"
            style={{ zIndex: Z_PWA_UPDATE_BANNER, paddingTop: "max(0.5rem, var(--sat))" }}
        >
            {needRefresh ? (
                <>
                    <span className="text-sm">A new version of MedExpert is available.</span>
                    <div className="flex gap-2">
                        <Button size="sm" variant="ghost" className="text-white hover:text-white/80" onClick={onDismissUpdate}>
                            Later
                        </Button>
                        <Button size="sm" className="bg-white text-[var(--color-brand-wMain)] hover:bg-white/90" onClick={onAcceptUpdate}>
                            <RefreshCw className="mr-1 h-3 w-3" />
                            Update now
                        </Button>
                    </div>
                </>
            ) : (
                <>
                    <span className="text-sm">MedExpert is ready for offline use.</span>
                    <button onClick={onDismissOfflineReady} className="p-1">
                        <X className="h-4 w-4" />
                    </button>
                </>
            )}
        </div>
    );
}
