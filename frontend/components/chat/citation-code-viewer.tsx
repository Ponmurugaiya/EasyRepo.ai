"use client";

import { useEffect, useState } from "react";
import { X, FileCode2, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "../../lib/utils";
import { getEntitySource } from "../../lib/api";
import type { ResolvedCitation } from "../../lib/citations";
import type { EntitySourceResponse } from "../../types/api";

interface CitationCodeViewerProps {
  repoId: string;
  citation: ResolvedCitation | null;
  citationIndex: number | null;
  onClose: () => void;
}

export function CitationCodeViewer({
  repoId,
  citation,
  citationIndex,
  onClose,
}: CitationCodeViewerProps) {
  const [entitySource, setEntitySource] = useState<EntitySourceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const entityId =
    citation && "matched_entity_id" in citation
      ? citation.matched_entity_id
      : null;

  useEffect(() => {
    if (!citation || !entityId) {
      setEntitySource(null);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);
    setEntitySource(null);

    getEntitySource(repoId, entityId)
      .then((data) => {
        setEntitySource(data);
      })
      .catch((err: Error) => {
        setError(err.message ?? "Failed to load source code");
      })
      .finally(() => setLoading(false));
  }, [repoId, entityId]);

  if (!citation) return null;

  const citedStart = citation.start_line;
  const citedEnd = citation.end_line;

  // Lines to render: entity source split by line, or fall back to empty
  const sourceLines = entitySource
    ? entitySource.source.split("\n")
    : [];

  // The entity's own start_line offset lets us compute absolute line numbers
  const entityStartLine = entitySource?.start_line ?? 1;

  const label =
    citationIndex != null ? `[${citationIndex}]` : citation.raw;

  const kindColor =
    citation.kind === "unsupported"
      ? "text-amber-400 border-amber-700/50"
      : "text-green-400 border-green-700/50";

  return (
    // Backdrop
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Panel */}
      <div
        className="relative flex flex-col w-full max-w-3xl max-h-[80vh] mx-4 rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-700/60 shrink-0">
          <FileCode2 className={cn("h-4 w-4 shrink-0", kindColor.split(" ")[0])} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-sm text-zinc-200 truncate">
                {citation.file_path}
              </span>
              <span className="font-mono text-xs text-zinc-500 shrink-0">
                :{citedStart}{citedEnd !== citedStart ? `–${citedEnd}` : ""}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium border",
                  citation.kind === "definition"
                    ? "bg-blue-900/40 text-blue-300 border-blue-700/50"
                    : citation.kind === "callsite"
                    ? "bg-purple-900/40 text-purple-300 border-purple-700/50"
                    : "bg-amber-900/40 text-amber-300 border-amber-700/50"
                )}
              >
                {citation.kind === "definition"
                  ? "definition"
                  : citation.kind === "callsite"
                  ? "call site"
                  : "unsupported"}
              </span>
            </div>
            {"matched_entity_name" in citation && citation.matched_entity_name && (
              <p className="text-xs text-zinc-500 mt-0.5 truncate">
                {citation.matched_entity_name}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-700 hover:text-zinc-200 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-16 text-zinc-500">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span className="text-sm">Loading source…</span>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 px-4 py-6 text-amber-400">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="text-sm">{error}</span>
            </div>
          )}

          {citation.kind === "unsupported" && !entityId && (
            <div className="px-4 py-6 text-sm text-amber-400">
              <p className="font-medium mb-1">Citation could not be verified</p>
              {"reason" in citation && (
                <p className="text-amber-600">{citation.reason}</p>
              )}
            </div>
          )}

          {entitySource && !loading && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono border-collapse">
                <tbody>
                  {sourceLines.map((line, idx) => {
                    const absoluteLine = entityStartLine + idx;
                    const isHighlighted =
                      absoluteLine >= citedStart && absoluteLine <= citedEnd;

                    return (
                      <tr
                        key={idx}
                        className={cn(
                          isHighlighted
                            ? "bg-blue-500/15"
                            : "hover:bg-zinc-800/40"
                        )}
                      >
                        {/* Line number */}
                        <td
                          className={cn(
                            "select-none px-3 py-px text-right w-12 shrink-0 border-r",
                            isHighlighted
                              ? "text-blue-400 border-blue-500/30"
                              : "text-zinc-600 border-zinc-700/40"
                          )}
                        >
                          {absoluteLine}
                        </td>
                        {/* Source line */}
                        <td
                          className={cn(
                            "px-4 py-px whitespace-pre",
                            isHighlighted ? "text-zinc-100" : "text-zinc-300"
                          )}
                        >
                          {line}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Footer hint */}
        <div className="shrink-0 px-4 py-2 border-t border-zinc-700/40 text-xs text-zinc-600">
          Citation {label} · {label} highlighted in blue
        </div>
      </div>
    </div>
  );
}
