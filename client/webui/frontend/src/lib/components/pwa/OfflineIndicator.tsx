import { WifiOff } from "lucide-react";
import { useOnlineStatus } from "@/lib/hooks";
import { Z_OFFLINE_INDICATOR } from "@/lib/constants/zIndex";

export function OfflineIndicator() {
    const isOnline = useOnlineStatus();
    if (isOnline) return null;

    return (
        <div
            className="fixed bottom-[calc(56px+var(--sab)+0.5rem)] right-4 flex items-center gap-2 rounded-lg bg-destructive px-3 py-2 text-sm text-white shadow-lg md:bottom-4"
            style={{ zIndex: Z_OFFLINE_INDICATOR }}
        >
            <WifiOff className="h-4 w-4" />
            <span>Offline</span>
        </div>
    );
}
