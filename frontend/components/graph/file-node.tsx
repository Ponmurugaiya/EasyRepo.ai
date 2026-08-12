"use client";

import { memo, useRef, useEffect } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { ChevronDown, ChevronRight, FileCode2 } from "lucide-react";
import { cn } from "../../lib/utils";
import type { FileNode, InlineEntity } from "../../types/graph";

// ── Language styling ──────────────────────────────────────────────────────────

const LANG_BORDER: Record<string, string> = {
  python:     "border-blue-500/60",
  typescript: "border-emerald-500/60",
  markdown:   "border-zinc-600/50",
};
const LANG_HEADER_BG: Record<string, string> = {
  python:     "bg-blue-950/70",
  typescript: "bg-emerald-950/70",
  markdown:   "bg-zinc-900/70",
};
const LANG_BADGE: Record<string, string> = {
  python:     "bg-blue-900/80 text-blue-300",
  typescript: "bg-emerald-900/80 text-emerald-300",
  markdown:   "bg-zinc-800 text-zinc-400",
};
const LANG_LABEL: Record<string, string> = {
  python: "py", typescript: "ts", markdown: "md",
};

// ── Entity type → highlight color band ───────────────────────────────────────

const ENTITY_BG: Record<string, string> = {
  class:     "bg-purple-500/12",
  interface: "bg-sky-500/12",
  function:  "bg-green-500/12",
  method:    "bg-teal-500/12",
  variable:  "bg-orange-500/10",
  doc_block: "bg-zinc-500/8",
};
const ENTITY_BG_HOVER: Record<string, string> = {
  class:     "hover:bg-purple-500/20",
  interface: "hover:bg-sky-500/20",
  function:  "hover:bg-green-500/20",
  method:    "hover:bg-teal-500/20",
  variable:  "hover:bg-orange-500/18",
  doc_block: "hover:bg-zinc-500/15",
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface FileNodeData extends FileNode {
  onEntityClick: (entity: InlineEntity, fileId: string) => void;
  onToggleExpand: (id: string) => void;   // passed from panel — no store in node
  isExpanded: boolean;                    // passed from panel
  isHighlighted: boolean;
  highlightedEntityId: string | null;
  isHovered: boolean;
}

// Max lines shown in collapsed-source preview
const PREVIEW_LINES = 24;

// ── Component ─────────────────────────────────────────────────────────────────

function FileNodeComponent({ id, data }: NodeProps<FileNodeData>) {
  const isExpanded = data.isExpanded;
  const codeRef = useRef<HTMLDivElement>(null);

  const safeEntities = data.entities ?? [];
  const sourceLines  = (data.source ?? "").split("\n");
  const totalLines   = sourceLines.length;
  const hasSource    = totalLines > 1 || (sourceLines[0] ?? "").length > 0;

  const border    = LANG_BORDER[data.language]    ?? LANG_BORDER.python;
  const headerBg  = LANG_HEADER_BG[data.language] ?? LANG_HEADER_BG.python;
  const badge     = LANG_BADGE[data.language]     ?? LANG_BADGE.python;
  const langLabel = LANG_LABEL[data.language]     ?? data.language;

  // Build a map: line number → entity (for highlighting)
  // Each source line can belong to at most one entity (innermost wins)
  const lineEntityMap = new Map<number, InlineEntity>();
  // Process in reverse so outer entities don't overwrite inner ones
  for (const ent of [...safeEntities].reverse()) {
    for (let ln = ent.start_line; ln <= ent.end_line; ln++) {
      lineEntityMap.set(ln, ent);
    }
  }

  // Scroll highlighted entity into view when highlighted from citation
  useEffect(() => {
    if (!data.highlightedEntityId || !codeRef.current || !isExpanded) return;
    const hlEnt = safeEntities.find((e) => e.id === data.highlightedEntityId);
    if (!hlEnt) return;
    const lineEl = codeRef.current.querySelector(
      `[data-line="${hlEnt.start_line}"]`
    ) as HTMLElement | null;
    if (lineEl) {
      lineEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [data.highlightedEntityId, isExpanded, safeEntities]);

  const visibleLines = isExpanded ? sourceLines : sourceLines.slice(0, PREVIEW_LINES);

  return (
    <div
      className={cn(
        "rounded-lg border-2 shadow-xl bg-zinc-950 overflow-hidden flex flex-col",
        border,
        data.isHighlighted && "ring-2 ring-yellow-400/80 ring-offset-1 ring-offset-zinc-950",
        data.isHovered && "shadow-2xl",
      )}
      style={{ width: 320, minWidth: 320 }}
    >
      {/* ── Connection handles (file level) ── */}
      <Handle type="target" position={Position.Top}
        className="!w-2.5 !h-2.5 !bg-zinc-500 !border-zinc-400" />
      <Handle type="source" position={Position.Bottom}
        className="!w-2.5 !h-2.5 !bg-zinc-500 !border-zinc-400" />

      {/* ── File header ── */}
      <div
        className={cn(
          "flex items-center gap-2 px-3 py-2 shrink-0 border-b border-zinc-800",
          headerBg,
          hasSource ? "cursor-pointer" : ""
        )}
        onClick={() => hasSource && data.onToggleExpand(id)}
      >
        <FileCode2 className="h-3.5 w-3.5 shrink-0 text-zinc-300" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="font-bold text-xs text-white truncate">{data.name}</span>
            {data.is_entry && (
              <span className="shrink-0 text-[9px] font-bold px-1.5 py-px rounded
                               bg-green-800 text-green-200 border border-green-700/50">
                ▶ ENTRY
              </span>
            )}
            <span className={cn("shrink-0 text-[9px] font-mono font-bold px-1 py-px rounded", badge)}>
              {langLabel}
            </span>
          </div>
          <p className="text-[9px] text-zinc-500 font-mono truncate leading-tight mt-px">
            {data.file_path}
          </p>
        </div>
        {hasSource && (
          <button
            className="shrink-0 text-zinc-500 hover:text-zinc-200 transition-colors"
            onClick={(e) => { e.stopPropagation(); data.onToggleExpand(id); }}
          >
            {isExpanded
              ? <ChevronDown className="h-3 w-3" />
              : <ChevronRight className="h-3 w-3" />}
          </button>
        )}
      </div>

      {/* ── Source code view ── */}
      {hasSource ? (
        <div
          ref={codeRef}
          className={cn(
            "overflow-y-auto overflow-x-hidden",
            isExpanded ? "max-h-96" : "max-h-40",
          )}
        >
          <table className="w-full border-collapse text-[10px] font-mono leading-4">
            <tbody>
              {visibleLines.map((lineText, idx) => {
                const lineNum  = idx + 1;
                const ent      = lineEntityMap.get(lineNum);
                const isHlLine = !!ent && ent.id === data.highlightedEntityId;
                const isEntStart = ent?.start_line === lineNum;

                return (
                  <tr
                    key={lineNum}
                    data-line={lineNum}
                    className={cn(
                      "group relative",
                      isHlLine
                        ? "bg-yellow-500/20"
                        : ent
                        ? cn(
                            ENTITY_BG[ent.type] ?? "bg-zinc-800/20",
                            ENTITY_BG_HOVER[ent.type] ?? "hover:bg-zinc-800/30",
                            "cursor-pointer"
                          )
                        : "hover:bg-zinc-900/60"
                    )}
                    onClick={() => {
                      if (ent) data.onEntityClick(ent, id);
                    }}
                  >
                    {/* Line number — left border acts as entity type color bar */}
                    <td
                      className="select-none pl-1.5 pr-2 py-px text-right w-8 align-top
                                 shrink-0 border-r border-zinc-800/60 border-l-2 text-zinc-600"
                      style={{
                        borderLeftColor: isHlLine
                          ? "#facc15"
                          : ent
                          ? (({
                              class:     "#c084fc",
                              interface: "#38bdf8",
                              function:  "#4ade80",
                              method:    "#2dd4bf",
                              variable:  "#fb923c",
                              doc_block: "#71717a",
                            } as Record<string, string>)[ent.type] ?? "#52525b")
                          : "transparent",
                      }}
                    >
                      {lineNum}
                    </td>

                    {/* Source text */}
                    <td className="pl-2 pr-1 py-px whitespace-pre overflow-hidden
                                   text-ellipsis max-w-[250px] align-top">
                      <span className={cn(
                        isHlLine ? "text-yellow-200" : ent ? "text-zinc-200" : "text-zinc-400"
                      )}>
                        {lineText || " "}
                      </span>
                    </td>

                    {/* Entity name badge on start line */}
                    {isEntStart && ent && (
                      <td className="pr-1 py-px align-top shrink-0">
                        <span className={cn(
                          "text-[8px] font-semibold px-1 py-px rounded opacity-70",
                          isHlLine
                            ? "text-yellow-300 bg-yellow-900/40"
                            : "text-zinc-400 bg-zinc-800/60"
                        )}>
                          {ent.name}
                        </span>
                      </td>
                    )}
                    {(!isEntStart || !ent) && (
                      <td className="w-2" />
                    )}
                  </tr>
                );
              })}

              {/* "…N more lines" row when collapsed */}
              {!isExpanded && totalLines > PREVIEW_LINES && (
                <tr
                  className="cursor-pointer hover:bg-zinc-800/60"
                  onClick={() => data.onToggleExpand(id)}
                >
                  <td />
                  <td className="pl-2 pr-1 py-1 text-zinc-600" colSpan={3}>
                    ···  {totalLines - PREVIEW_LINES} more lines — click to expand
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="px-3 py-2 text-[10px] text-zinc-700 italic">
          no source available
        </div>
      )}
    </div>
  );
}

export const FileNodeComponent_ = memo(FileNodeComponent);
