import { Activity } from "lucide-react";
import { Z_TRIAGE_BANNER } from "@/lib/constants/zIndex";

interface TriageFloatingBannerProps {
    visible: boolean;
    onTap: () => void;
}

export function TriageFloatingBanner({ visible, onTap }: TriageFloatingBannerProps) {
    if (!visible) return null;

    return (
        <button
            onClick={onTap}
            className="fixed inset-x-4 bottom-[calc(56px+var(--sab)+0.5rem)] mx-auto flex max-w-sm items-center justify-center gap-2 rounded-full bg-[var(--color-brand-wMain)] px-4 py-2.5 text-sm font-medium text-white shadow-lg transition-transform active:scale-95"
            style={{ zIndex: Z_TRIAGE_BANNER }}
        >
            <Activity className="h-4 w-4 animate-pulse" />
            <span>Triage in progress — Tap to view</span>
        </button>
    );
}
