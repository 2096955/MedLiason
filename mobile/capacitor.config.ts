import type { CapacitorConfig } from "@capacitor/cli";

/**
 * MedExpert Capacitor Configuration
 *
 * Two operational modes:
 * - Mode A (Remote WebView): Set MEDEXPERT_SERVER_URL to the deployed URL.
 *   The WebView navigates directly to the server (NOT iframe — see plan R1).
 * - Mode B (Bundled Assets): Omit MEDEXPERT_SERVER_URL.
 *   The WebView serves from the local www/ directory.
 *
 * NOTE: Environment variables (MEDEXPERT_SERVER_URL, NODE_ENV) must be
 * set in the shell environment before running `npx cap sync`.
 * The npm scripts (dev:ios, dev:android) set these inline.
 * For CI/CD, export them or use: source .env && npx cap sync
 */
const serverUrl = process.env.MEDEXPERT_SERVER_URL;

const config: CapacitorConfig = {
    appId: "com.medexpert.research",
    appName: "MedExpert",
    webDir: "www",

    server: {
        // Mode A: remote URL. Mode B: omit url to serve from webDir.
        ...(serverUrl ? { url: serverUrl } : {}),

        allowNavigation: [
            "medexpert-*.run.app",
            "*.medexpert.example.com",
            "accounts.google.com",
            "login.microsoftonline.com",
        ],

        androidScheme: "https",
        cleartext: process.env.NODE_ENV === "development",
    },

    // CRITICAL: Do NOT enable CapacitorHttp. It does not support EventSource/SSE.
    // MedExpert's streaming pipeline relies on native browser EventSource.
    plugins: {
        CapacitorHttp: {
            enabled: false,
        },
        CapacitorCookies: {
            enabled: true,
        },
        StatusBar: {
            style: "LIGHT",
            backgroundColor: "#015B82",
        },
        Keyboard: {
            resize: "body",
            resizeOnFullScreen: true,
        },
        PushNotifications: {
            presentationOptions: ["badge", "sound", "alert"],
        },
    },

    // WCAG 2.1 SC 1.4.4 — clinicians may need to zoom medical text
    // Overrides reference repo's user-scalable=no (R5)

    ios: {
        contentInset: "automatic",
        allowsLinkPreview: true,
        scrollEnabled: true,
        preferredContentMode: "mobile",
        webContentsDebuggingEnabled: process.env.NODE_ENV === "development",
    },

    android: {
        allowMixedContent: false,
        webContentsDebuggingEnabled: process.env.NODE_ENV === "development",
    },
};

export default config;
