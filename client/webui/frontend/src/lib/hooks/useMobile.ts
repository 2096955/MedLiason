import React from "react";

const MOBILE_BREAKPOINT = 768;

export function useIsMobile() {
    const [isMobile, setIsMobile] = React.useState<boolean>(
        () => typeof window !== "undefined" && window.innerWidth < MOBILE_BREAKPOINT
    );

    React.useEffect(() => {
        const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
        const update = () => {
            setIsMobile(window.innerWidth < MOBILE_BREAKPOINT);
        };
        mql.addEventListener("change", update);
        // Fallback: some mobile browsers don't fire matchMedia reliably on rotation
        window.addEventListener("resize", update);
        update();
        return () => {
            mql.removeEventListener("change", update);
            window.removeEventListener("resize", update);
        };
    }, []);

    return isMobile;
}
