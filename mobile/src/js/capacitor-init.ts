/**
 * Capacitor plugin initialization — called once on app startup.
 * Every installed plugin is explicitly initialized here (R11).
 */
import { Capacitor } from "@capacitor/core";
import { StatusBar, Style } from "@capacitor/status-bar";
import { Keyboard, KeyboardResize } from "@capacitor/keyboard";
import { App } from "@capacitor/app";
import { Haptics, ImpactStyle, NotificationType } from "@capacitor/haptics";

export async function initCapacitor(): Promise<void> {
    if (!Capacitor.isNativePlatform()) return;

    // Status bar: brand color with light text
    try {
        await StatusBar.setBackgroundColor({ color: "#015B82" });
        await StatusBar.setStyle({ style: Style.Light });
    } catch (e) {
        console.warn("[Capacitor] StatusBar init failed:", e);
    }

    // Keyboard: resize body mode
    try {
        await Keyboard.setResizeMode({ mode: KeyboardResize.Body });
    } catch (e) {
        console.warn("[Capacitor] Keyboard init failed:", e);
    }

    // Expose haptics bridge to the React app via window
    (window as any).__medexpert_haptics = {
        light: () => Haptics.impact({ style: ImpactStyle.Light }),
        medium: () => Haptics.impact({ style: ImpactStyle.Medium }),
        heavy: () => Haptics.impact({ style: ImpactStyle.Heavy }),
        success: () => Haptics.notification({ type: NotificationType.Success }),
        error: () => Haptics.notification({ type: NotificationType.Error }),
    };

    console.log("[Capacitor] Initialized — platform:", Capacitor.getPlatform());
}
