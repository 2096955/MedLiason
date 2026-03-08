import React from "react";

const LEGEND_ITEMS = [
    { label: "Disease", color: "#ef4444", shape: "rounded-full" },
    { label: "Drug", color: "#3b82f6", shape: "rotate-45 rounded-sm" },
    { label: "Gene", color: "#22c55e", shape: "rotate-45 rounded-none" },
    { label: "Study", color: "#f59e0b", shape: "rounded-sm" },
    { label: "Session", color: "#a855f7", shape: "rounded-full" },
    { label: "Specialist", color: "#06b6d4", shape: "rounded-full" },
] as const;

const GraphLegend: React.FC = () => (
    <div className="absolute bottom-4 left-4 z-10 rounded-md border border-border bg-background/90 px-3 py-2.5 text-xs backdrop-blur">
        <div className="mb-1.5 font-medium text-muted-foreground">Entity Types</div>
        <div className="flex flex-col gap-1.5">
            {LEGEND_ITEMS.map(({ label, color, shape }) => (
                <div key={label} className="flex items-center gap-2">
                    <div
                        className={`h-2.5 w-2.5 shrink-0 ${shape}`}
                        style={{ backgroundColor: color }}
                    />
                    <span className="text-foreground/80">{label}</span>
                </div>
            ))}
        </div>
    </div>
);

export default GraphLegend;
