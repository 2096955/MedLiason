import React, { useCallback, useRef, useEffect, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface MobileBottomSheetProps {
    open: boolean;
    onClose: () => void;
    title?: string;
    children: React.ReactNode;
    height?: "half" | "full" | "auto";
    showHandle?: boolean;
}

export function MobileBottomSheet({
    open,
    onClose,
    title,
    children,
    height = "full",
    showHandle = true,
}: MobileBottomSheetProps) {
    const sheetRef = useRef<HTMLDivElement>(null);
    const [isVisible, setIsVisible] = useState(false);
    const [isAnimating, setIsAnimating] = useState(false);

    useEffect(() => {
        if (open) {
            setIsVisible(true);
            requestAnimationFrame(() => {
                requestAnimationFrame(() => setIsAnimating(true));
            });
        } else {
            setIsAnimating(false);
            const timer = setTimeout(() => setIsVisible(false), 300);
            return () => clearTimeout(timer);
        }
    }, [open]);

    const handleBackdropClick = useCallback(
        (e: React.MouseEvent) => {
            if (e.target === e.currentTarget) onClose();
        },
        [onClose]
    );

    if (!isVisible) return null;

    const heightClass = {
        half: "max-h-[50vh]",
        full: "max-h-[92vh]",
        auto: "max-h-[85vh]",
    }[height];

    return (
        <div
            className={cn(
                "fixed inset-0 z-50 transition-colors duration-300",
                isAnimating ? "bg-black/50" : "bg-black/0"
            )}
            onClick={handleBackdropClick}
        >
            <div
                ref={sheetRef}
                className={cn(
                    "fixed inset-x-0 bottom-0 flex flex-col rounded-t-2xl bg-background shadow-2xl transition-transform duration-300 ease-out",
                    heightClass,
                    isAnimating ? "translate-y-0" : "translate-y-full"
                )}
                style={{ paddingBottom: "var(--sab)" }}
            >
                {showHandle && (
                    <div className="flex justify-center pt-3 pb-1">
                        <div className="h-1 w-10 rounded-full bg-muted-foreground/30" />
                    </div>
                )}

                {title && (
                    <div className="flex items-center justify-between border-b px-4 py-3">
                        <h2 className="text-base font-semibold text-foreground">{title}</h2>
                        <button
                            type="button"
                            onClick={onClose}
                            className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-muted"
                            aria-label="Close"
                        >
                            <X className="h-5 w-5" />
                        </button>
                    </div>
                )}

                <div className="flex-1 overflow-y-auto overscroll-contain">{children}</div>
            </div>
        </div>
    );
}
