"use client";

import { useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "reactflow";
import { cn } from "@/lib/utils";
import type { EntityConnection } from "@/types/graph";

// Edge color by dominant relationship type
const EDGE_COLOR: Record<string, string> = {
  CALLS: "#3b82f6",        // blue-500
  INHERITS: "#a855f7",     // purple-500
  IMPLEMENTS: "#8b5cf6",   // violet-500
  INSTANTIATES: "#14b8a6", // teal-500
  IMPORTS: "#6b7280",      // gray-500
};

const EDGE_LABEL_BG: Record<string, string> = {
  CALLS: "bg-blue-900/80 text-blue-300 border-blue-700/50",
  INHERITS: "bg-purple-900/80 text-purple-300 border-purple-700/50",
  IMPLEMENTS: "bg-violet-900/80 text-violet-300 border-violet-700/50",
  INSTANTIATES: "bg-teal-900/80 text-teal-300 border-teal-700/50",
  IMPORTS: "bg-zinc-800/80 text-zinc-400 border-zinc-600/50",
};

export interface GraphEdgeData {
  dominant_type: string;
  rel_types: string[];
  connections: EntityConnection[];
  isHovered: boolean;
}

export function GraphEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}: EdgeProps<GraphEdgeData>) {
  const [tooltipVisible, setTooltipVisible] = useState(false);

  const dominantType = data?.dominant_type ?? "CALLS";
  const connections = data?.connections ?? [];
  const color = EDGE_COLOR[dominantType] ?? EDGE_COLOR.CALLS;
  const labelBg = EDGE_LABEL_BG[dominantType] ?? EDGE_LABEL_BG.CALLS;

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const strokeWidth = data?.isHovered ? 2.5 : 1.5;
  const opacity = data?.isHovered ? 1 : 0.7;

  return (
    <>
      {/* Invisible wider path for easier hover targeting */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        onMouseEnter={() => setTooltipVisible(true)}
        onMouseLeave={() => setTooltipVisible(false)}
        style={{ cursor: "pointer" }}
      />

      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth,
          opacity,
          strokeDasharray: dominantType === "IMPORTS" ? "6 3" : undefined,
          transition: "stroke-width 0.15s, opacity 0.15s",
        }}
      />

      <EdgeLabelRenderer>
        {/* Relationship type label */}
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
          }}
          onMouseEnter={() => setTooltipVisible(true)}
          onMouseLeave={() => setTooltipVisible(false)}
        >
          <span
            className={cn(
              "text-[9px] font-mono font-semibold px-1.5 py-0.5 rounded border",
              labelBg,
              "opacity-80 hover:opacity-100 cursor-default transition-opacity"
            )}
          >
            {dominantType}
          </span>

          {/* Tooltip on hover */}
          {tooltipVisible && connections.length > 0 && (
            <div
              className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 z-50 min-w-[200px] max-w-[320px]"
              style={{ pointerEvents: "none" }}
            >
              <div className="rounded-lg border border-zinc-700 bg-zinc-900/95 shadow-2xl p-2.5 backdrop-blur-sm">
                <p className="text-[10px] text-zinc-400 font-semibold mb-1.5">
                  {connections.length} connection{connections.length !== 1 ? "s" : ""}
                </p>
                <div className="space-y-1">
                  {connections.slice(0, 8).map((conn, i) => (
                    <div key={i} className="flex items-center gap-1.5 text-[10px]">
                      <span className="font-mono text-zinc-300 truncate max-w-[100px]">
                        {conn.from_entity_name}
                      </span>
                      <span className="shrink-0 text-zinc-600">→</span>
                      <span className="font-mono text-zinc-300 truncate max-w-[100px]">
                        {conn.to_entity_name}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 ml-auto text-[9px] px-1 rounded",
                          EDGE_LABEL_BG[conn.rel_type] ?? "bg-zinc-800 text-zinc-400 border-zinc-600"
                        )}
                      >
                        {conn.rel_type}
                      </span>
                    </div>
                  ))}
                  {connections.length > 8 && (
                    <p className="text-[10px] text-zinc-600 mt-1">
                      +{connections.length - 8} more
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
