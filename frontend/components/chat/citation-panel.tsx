"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { ChevronDown, CheckCircle2, AlertCircle, FileCode2 } from "lucide-react";
import type { ValidationReport } from "@/types/api";

interface CitationPanelProps {
  citations: ValidationReport;
}

export function CitationPanel({ citations }: CitationPanelProps) {
  const [open, setOpen] = useState(false);

  const { total_citations, definition_citations, call_site_citations, unsupported_citations, hallucination_rate } = citations;

  if (total_citations === 0) return null;

  const verifiedCount = definition_citations.length + call_site_citations.length;
  const hallCount = unsupported_citations.length;

  const hallPct = Math.round(hallucination_rate * 100);

  return (
    <div className="w-full rounded-xl border border-zinc-700/50 bg-zinc-900/60 overflow-hidden text-sm">
      {/* Summary row */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-zinc-800/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-green-400">
            <CheckCircle2 className="h-3.5 w-3.5" />
            <span className="text-xs">{verifiedCount} verified</span>
          </div>
          {hallCount > 0 && (
            <div className="flex items-center gap-1.5 text-amber-400">
              <AlertCircle className="h-3.5 w-3.5" />
              <span className="text-xs">{hallCount} unsupported</span>
            </div>
          )}
          <span className="text-xs text-zinc-600">
            {hallPct}% hallucination rate
          </span>
        </div>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-zinc-500 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {/* Expanded list */}
      {open && (
        <div className="border-t border-zinc-700/50 divide-y divide-zinc-700/30">
          {[
            ...definition_citations.map((c) => ({ ...c, kind: "definition" as const })),
            ...call_site_citations.map((c) => ({ ...c, kind: "callsite" as const })),
            ...unsupported_citations.map((c) => ({ ...c, kind: "unsupported" as const })),
          ].map((c, i) => (
            <div key={i} className="flex items-start gap-2.5 px-3 py-2">
              <FileCode2
                className={cn(
                  "mt-0.5 h-3.5 w-3.5 shrink-0",
                  c.kind === "unsupported" ? "text-amber-500" : "text-green-500"
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-zinc-300 truncate">
                    {c.file_path}:{c.start_line}
                    {c.end_line !== c.start_line ? `–${c.end_line}` : ""}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 rounded px-1 py-px text-[10px] font-medium",
                      c.kind === "definition"
                        ? "bg-blue-900/50 text-blue-300"
                        : c.kind === "callsite"
                        ? "bg-purple-900/50 text-purple-300"
                        : "bg-amber-900/50 text-amber-300"
                    )}
                  >
                    {c.kind === "definition"
                      ? "def"
                      : c.kind === "callsite"
                      ? "call"
                      : "unsupported"}
                  </span>
                </div>
                {/* Entity name */}
                {"matched_entity_name" in c && c.matched_entity_name && (
                  <p className="mt-0.5 text-xs text-zinc-500 truncate">
                    {c.matched_entity_name}
                  </p>
                )}
                {"reason" in c && c.reason && (
                  <p className="mt-0.5 text-xs text-amber-600 truncate">
                    {c.reason}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
