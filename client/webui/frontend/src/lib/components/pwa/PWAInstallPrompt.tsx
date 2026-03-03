import { useState, useEffect, useCallback } from "react";
import { Download, X, Share } from "lucide-react";
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/lib/components/ui";
import { Z_PWA_INSTALL_PROMPT } from "@/lib/constants/zIndex";

interface BeforeInstallPromptEvent extends Event {
    prompt(): Promise<void>;
    userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const DISMISS_KEY = "medexpert-pwa-install-dismissed";
const DISMISS_DURATION_MS = 7 * 24 * 60 * 60 * 1000;

function isIOS(): boolean {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !("MSStream" in window);
}

function isStandalone(): boolean {
    return (
        window.matchMedia("(display-mode: standalone)").matches ||
        ("standalone" in navigator && (navigator as Record<string, unknown>).standalone === true)
    );
}

interface PWAInstallPromptProps {
    visible?: boolean;
    onBannerStateChange?: (canInstall: boolean) => void;
}

export function PWAInstallPrompt({ visible = true, onBannerStateChange }: PWAInstallPromptProps) {
    const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
    const [showBanner, setShowBanner] = useState(false);
    const [showIOSModal, setShowIOSModal] = useState(false);

    useEffect(() => {
        onBannerStateChange?.(showBanner);
    }, [showBanner, onBannerStateChange]);

    useEffect(() => {
        if (isStandalone()) return;

        const dismissedAt = localStorage.getItem(DISMISS_KEY);
        if (dismissedAt && Date.now() - parseInt(dismissedAt) < DISMISS_DURATION_MS) return;

        const handler = (e: Event) => {
            e.preventDefault();
            setDeferredPrompt(e as BeforeInstallPromptEvent);
            setShowBanner(true);
        };
        window.addEventListener("beforeinstallprompt", handler);

        if (isIOS()) {
            const timer = setTimeout(() => setShowBanner(true), 30000);
            return () => {
                clearTimeout(timer);
                window.removeEventListener("beforeinstallprompt", handler);
            };
        }

        return () => window.removeEventListener("beforeinstallprompt", handler);
    }, []);

    const handleInstall = useCallback(async () => {
        if (deferredPrompt) {
            await deferredPrompt.prompt();
            const { outcome } = await deferredPrompt.userChoice;
            if (outcome === "accepted") setShowBanner(false);
            setDeferredPrompt(null);
        } else if (isIOS()) {
            setShowIOSModal(true);
        }
    }, [deferredPrompt]);

    const handleDismiss = useCallback(() => {
        setShowBanner(false);
        localStorage.setItem(DISMISS_KEY, Date.now().toString());
    }, []);

    if (!showBanner || !visible) return null;

    return (
        <>
            <div
                className="fixed bottom-20 left-4 right-4 mx-auto max-w-sm rounded-lg border bg-background p-4 shadow-lg sm:left-auto sm:right-4"
                style={{ zIndex: Z_PWA_INSTALL_PROMPT }}
            >
                <div className="flex items-start gap-3">
                    <Download className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-brand-wMain)]" />
                    <div className="flex-1">
                        <p className="text-sm font-medium">Install MedExpert</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                            {isIOS() ? "Add to your home screen for quick access" : "Install for faster access and offline app shell"}
                        </p>
                    </div>
                    <button onClick={handleDismiss} className="shrink-0 p-1">
                        <X className="h-4 w-4 text-muted-foreground" />
                    </button>
                </div>
                <div className="mt-3 flex gap-2">
                    <Button size="sm" variant="ghost" onClick={handleDismiss}>
                        Not now
                    </Button>
                    <Button size="sm" onClick={handleInstall}>
                        {isIOS() ? "Show me how" : "Install"}
                    </Button>
                </div>
            </div>

            <Dialog open={showIOSModal} onOpenChange={setShowIOSModal}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Install MedExpert</DialogTitle>
                        <DialogDescription>Add MedExpert to your home screen for quick access</DialogDescription>
                    </DialogHeader>
                    <ol className="space-y-3 text-sm">
                        <li className="flex items-center gap-2">
                            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-brand-wMain)] text-xs text-white">1</span>
                            <span>Tap the <Share className="inline h-4 w-4" /> Share button in Safari</span>
                        </li>
                        <li className="flex items-center gap-2">
                            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-brand-wMain)] text-xs text-white">2</span>
                            <span>Scroll down and tap &quot;Add to Home Screen&quot;</span>
                        </li>
                        <li className="flex items-center gap-2">
                            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-brand-wMain)] text-xs text-white">3</span>
                            <span>Tap &quot;Add&quot; to confirm</span>
                        </li>
                    </ol>
                </DialogContent>
            </Dialog>
        </>
    );
}
