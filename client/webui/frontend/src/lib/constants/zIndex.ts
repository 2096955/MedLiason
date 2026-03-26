/**
 * Shared z-index scale for fixed/absolute positioned overlays.
 * All mobile overlays MUST use these constants to prevent stacking conflicts.
 *
 * Mutual exclusion rule: Only one banner visible at a time.
 * Priority order: offline > update > install > triage
 * Enforced by useBannerPriority() hook.
 */

export const Z_SIDEBAR = 30;
export const Z_SIDE_PANEL_SHEET = 40;
export const Z_BOTTOM_NAV = 50;
export const Z_TRIAGE_BANNER = 51;
export const Z_OFFLINE_INDICATOR = 55;
export const Z_PWA_INSTALL_PROMPT = 60;
export const Z_PWA_UPDATE_BANNER = 70;
export const Z_SHEET_OVERLAY = 80;
export const Z_MODAL = 90;
