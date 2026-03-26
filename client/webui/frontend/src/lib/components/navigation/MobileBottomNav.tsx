import React from "react";
import { MessageSquare, Layers, Bot, History, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { Z_BOTTOM_NAV } from "@/lib/constants/zIndex";

export type MobileTabId = "chat" | "sources" | "agents" | "sessions" | "settings";

interface MobileTab {
    id: MobileTabId;
    label: string;
    icon: LucideIcon;
}

const MOBILE_TABS: MobileTab[] = [
    { id: "chat", label: "Chat", icon: MessageSquare },
    { id: "sources", label: "Sources", icon: Layers },
    { id: "agents", label: "Agents", icon: Bot },
    { id: "sessions", label: "Sessions", icon: History },
    { id: "settings", label: "Settings", icon: Settings },
];

interface MobileBottomNavProps {
    activeTab: MobileTabId;
    onTabChange: (tabId: MobileTabId) => void;
}

export const MobileBottomNav: React.FC<MobileBottomNavProps> = ({ activeTab, onTabChange }) => {
    return (
        <nav
            className="fixed inset-x-0 bottom-0 flex h-[calc(56px+var(--sab))] items-start border-t border-[var(--color-secondary-w70)] bg-[var(--color-primary-w100)]"
            style={{ zIndex: Z_BOTTOM_NAV, paddingBottom: "var(--sab)" }}
            role="tablist"
            aria-label="Main navigation"
        >
            {MOBILE_TABS.map((tab) => {
                const isActive = activeTab === tab.id;
                const Icon = tab.icon;

                return (
                    <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        aria-selected={isActive}
                        aria-label={tab.label}
                        onClick={() => onTabChange(tab.id)}
                        className={cn(
                            "flex h-14 flex-1 flex-col items-center justify-center gap-0.5 transition-colors",
                            isActive
                                ? "text-[var(--color-brand-wMain)]"
                                : "text-[var(--color-primary-text-w10)] hover:text-[var(--color-background-w10)]"
                        )}
                    >
                        <Icon className="h-5 w-5" />
                        <span className="text-[10px] leading-tight">{tab.label}</span>
                    </button>
                );
            })}
        </nav>
    );
};
