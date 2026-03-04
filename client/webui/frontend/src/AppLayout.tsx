import { useCallback, useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { NavigationSidebar, ToastContainer, bottomNavigationItems, getTopNavigationItems, EmptyState } from "@/lib/components";
import { MobileBottomNav, type MobileTabId } from "@/lib/components/navigation";
import { PWAUpdateBanner, PWAInstallPrompt, OfflineIndicator } from "@/lib/components/pwa";
import { TriageFloatingBanner } from "@/lib/components/chat/triage";
import { SelectionContextMenu, useTextSelection } from "@/lib/components/chat/selection";
import { ChatProvider } from "@/lib/providers";
import { useAuthContext, useBeforeUnload, useChatContext, useConfigContext, useIsMobile, useOnlineStatus } from "@/lib/hooks";
import { usePWA } from "@/lib/hooks/usePWA";
import { useBannerPriority } from "@/lib/hooks/useBannerPriority";
import { cn } from "@/lib/utils";

function AppLayoutContent() {
    const location = useLocation();
    const navigate = useNavigate();
    const { isAuthenticated, login, useAuthorization } = useAuthContext();
    const { configFeatureEnablement } = useConfigContext();
    const { isMenuOpen, menuPosition, selectedText, sourceTaskId, clearSelection } = useTextSelection();

    // Mobile & PWA hooks
    const isMobile = useIsMobile();
    const isOnline = useOnlineStatus();
    const { needRefresh, offlineReady, acceptUpdate, dismissUpdate, dismissOfflineReady } = usePWA();
    const [canInstall, setCanInstall] = useState(false);

    // Triage state from ChatContext (AppLayoutContent is inside ChatProvider)
    const { selectedAgentName, isResponding, triageProgress, openSidePanelTab } = useChatContext();
    const triageActive = (selectedAgentName?.includes("Triage") ?? false) && isResponding && triageProgress !== null;

    // Banner mutual exclusion — one banner at a time
    const activeBanner = useBannerPriority({
        isOffline: !isOnline,
        needsUpdate: needRefresh,
        canInstall,
        triageActive,
    });

    // Streaming guard: don't reload during active research pipeline
    const safeAcceptUpdate = useCallback(() => {
        if (isResponding) return;
        acceptUpdate();
    }, [isResponding, acceptUpdate]);

    // Mobile bottom nav state
    const [activeMobileTab, setActiveMobileTab] = useState<MobileTabId>("chat");

    // Sync tab highlight with current route
    useEffect(() => {
        const path = location.pathname;
        if (path === "/" || path.startsWith("/chat")) setActiveMobileTab("chat");
        else if (path.startsWith("/agents")) setActiveMobileTab("agents");
    }, [location.pathname]);

    const handleMobileTabChange = useCallback(
        (tabId: MobileTabId) => {
            setActiveMobileTab(tabId);
            switch (tabId) {
                case "chat":
                    navigate("/chat");
                    break;
                case "sources":
                    openSidePanelTab("rag");
                    break;
                case "agents":
                    navigate("/agents");
                    break;
                case "sessions":
                    navigate("/chat");
                    break;
                case "settings":
                    window.dispatchEvent(new CustomEvent("open-settings-dialog"));
                    break;
            }
        },
        [navigate, openSidePanelTab]
    );

    const handleTriageTap = useCallback(() => {
        openSidePanelTab("triage");
    }, [openSidePanelTab]);

    // Temporary fix: Radix dialogs sometimes leave pointer-events: none on body when closed
    useEffect(() => {
        const observer = new MutationObserver(() => {
            const bodyStyle = document.body.style.pointerEvents;
            const hasOpenOverlays = document.querySelector('[data-state="open"]');

            // If no overlays (dialogs, popovers, etc.) are open but pointer-events is set to none, remove it
            if (!hasOpenOverlays && bodyStyle === "none") {
                document.body.style.removeProperty("pointer-events");
            }
        });

        // Observe changes to body styles and DOM changes for overlays
        observer.observe(document.body, {
            attributes: true,
            attributeFilter: ["style"],
            childList: true,
            subtree: true,
        });

        return () => {
            observer.disconnect();
        };
    }, []);

    // Get navigation items based on feature flags
    const topNavItems = getTopNavigationItems(configFeatureEnablement);

    // Enable beforeunload warning when chat data is present
    useBeforeUnload();

    const getActiveItem = () => {
        const path = location.pathname;
        if (path === "/" || path.startsWith("/chat")) return "chat";
        if (path.startsWith("/projects")) return "projects";
        if (path.startsWith("/prompts")) return "prompts";
        if (path.startsWith("/agents")) return "agentMesh";
        if (path.startsWith("/knowledge-graph")) return "knowledgeGraph";
        return "chat";
    };

    if (useAuthorization && !isAuthenticated) {
        return (
            <EmptyState
                variant="noImage"
                title="Welcome to MedExpert!"
                className="h-screen w-screen"
                buttons={[
                    {
                        text: "Login",
                        onClick: () => login(),
                        variant: "default",
                    },
                ]}
            />
        );
    }

    const handleNavItemChange = (itemId: string) => {
        const item = topNavItems.find((item) => item.id === itemId) || bottomNavigationItems.find((item) => item.id === itemId);

        if (item?.onClick && itemId !== "settings") {
            item.onClick();
        } else if (itemId !== "settings") {
            const routeMap: Record<string, string> = {
                agentMesh: "agents",
                knowledgeGraph: "knowledge-graph",
            };
            navigate(`/${routeMap[itemId] || itemId}`);
        }
    };

    const handleHeaderClick = () => {
        navigate("/chat");
    };

    return (
        <div className="relative flex h-screen">
            {/* Desktop sidebar — hidden on mobile */}
            {!isMobile && (
                <NavigationSidebar
                    items={topNavItems}
                    bottomItems={bottomNavigationItems}
                    activeItem={getActiveItem()}
                    onItemChange={handleNavItemChange}
                    onHeaderClick={handleHeaderClick}
                />
            )}

            <main className={cn("h-full w-full flex-1 overflow-auto", isMobile && "pb-14", activeBanner === "update" && "pt-12")}>
                <Outlet />
            </main>

            {/* PWA Update Banner — fixed top, z-70. Only when priority allows */}
            {activeBanner === "update" && (
                <PWAUpdateBanner
                    needRefresh={needRefresh}
                    offlineReady={false}
                    onAcceptUpdate={safeAcceptUpdate}
                    onDismissUpdate={dismissUpdate}
                    onDismissOfflineReady={dismissOfflineReady}
                />
            )}

            {/* Offline-ready one-shot notification — show when no higher-priority banner */}
            {activeBanner === null && offlineReady && (
                <PWAUpdateBanner
                    needRefresh={false}
                    offlineReady={true}
                    onAcceptUpdate={safeAcceptUpdate}
                    onDismissUpdate={dismissUpdate}
                    onDismissOfflineReady={dismissOfflineReady}
                />
            )}

            {/* PWA Install Prompt — always mounted for event listener, visibility gated */}
            <PWAInstallPrompt visible={activeBanner === "install" || activeBanner === null} onBannerStateChange={setCanInstall} />

            {/* Offline Indicator — self-hides when online */}
            <OfflineIndicator />

            {/* Triage banner — mobile only, above bottom nav */}
            {isMobile && <TriageFloatingBanner visible={activeBanner === "triage"} onTap={handleTriageTap} />}

            {/* Mobile bottom nav — replaces desktop sidebar */}
            {isMobile && <MobileBottomNav activeTab={activeMobileTab} onTabChange={handleMobileTabChange} />}

            <ToastContainer />
            <SelectionContextMenu isOpen={isMenuOpen} position={menuPosition} selectedText={selectedText || ""} sourceTaskId={sourceTaskId} onClose={clearSelection} />
        </div>
    );
}

function AppLayout() {
    return (
        <ChatProvider>
            <AppLayoutContent />
        </ChatProvider>
    );
}

export default AppLayout;
