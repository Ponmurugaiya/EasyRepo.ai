"use client";

import { useEffect, useState } from "react";
import { cn } from "../../lib/utils";
import {
  Search, Cpu, CheckCircle2, Loader2, FileText, Lightbulb, Brain,
} from "lucide-react";
import type { AskJobProgress } from "../../lib/api";

// ── Stage definitions ─────────────────────────────────────────────────────────

interface StageDef {
  key: string;
  icon: React.ElementType;
  label: (progress?: AskJobProgress) => string;
  detail: string;
}

// Shared first and last stages
const STAGE_CLASSIFYING: StageDef = {
  key: "classifying",
  icon: Brain,
  label: () => "Classifying query",
  detail: "Understanding your question to pick the best search strategy…",
};

const STAGE_CITATIONS: StageDef = {
  key: "citations",
  icon: CheckCircle2,
  label: () => "Validating citations",
  detail: "Verifying every file and line reference against the code graph…",
};

// Overview pipeline stages
const OVERVIEW_STAGES: StageDef[] = [
  STAGE_CLASSIFYING,
  {
    key: "reading_files",
    icon: FileText,
    label: (p) =>
      p && p.files_total > 0
        ? `Reading files  ${p.files_done} / ${p.files_total}`
        : "Reading files…",
    detail: "Summarising every file in the repository…",
  },
  {
    key: "insights",
    icon: Lightbulb,
    label: () => "Getting required insights",
    detail: "Aggregating file summaries into folder-level understanding…",
  },
  {
    key: "generating",
    icon: Cpu,
    label: () => "Generating final response",
    detail: "Synthesising all insights into a complete answer…",
  },
  STAGE_CITATIONS,
];

// Semantic search pipeline stages
const SEMANTIC_STAGES: StageDef[] = [
  STAGE_CLASSIFYING,
  {
    key: "searching",
    icon: Search,
    label: () => "Searching the code graph",
    detail: "Running semantic search across entities and embeddings…",
  },
  {
    key: "generating",
    icon: Cpu,
    label: () => "Generating final response",
    detail: "Sending context to the language model…",
  },
  STAGE_CITATIONS,
];

// Fallback — used before we know the pipeline (pending/no progress yet)
const FALLBACK_STAGES = SEMANTIC_STAGES;

// Reassurance copy shown when the wait is long
const LONG_WAIT_COPY = [
  "Complex queries with deep call chains take a moment…",
  "Hang tight — processing large repos takes a bit longer…",
  "Still working — the model is reading a lot of context…",
  "Almost there — finalising the answer now…",
];

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

// Map backend stage key → index in each stage list
function stageIndex(stages: StageDef[], key: string): number {
  const idx = stages.findIndex((s) => s.key === key);
  // If the key isn't found yet (e.g. still "classifying" before first write),
  // return 0 so we show the first stage.
  return idx >= 0 ? idx : 0;
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ThinkingIndicatorProps {
  /** Live pipeline progress from the backend poll. Undefined while pending. */
  progress?: AskJobProgress;
}

export function ThinkingIndicator({ progress }: ThinkingIndicatorProps) {
  const [elapsedMs, setElapsedMs] = useState(0);
  const [longWaitIdx, setLongWaitIdx] = useState(0);

  // Elapsed timer — ticks every second
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsedMs(Date.now() - start), 1000);
    return () => clearInterval(id);
  }, []);

  // Rotate reassurance copy every 8s once waiting > 15s
  useEffect(() => {
    if (elapsedMs < 15000) return;
    const id = setInterval(
      () => setLongWaitIdx((i) => (i + 1) % LONG_WAIT_COPY.length),
      8000,
    );
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elapsedMs >= 15000]);

  // Pick stage list based on pipeline type from progress
  const stages =
    progress?.pipeline === "overview"
      ? OVERVIEW_STAGES
      : FALLBACK_STAGES;

  // Active stage index driven by backend progress; fallback to 0 (classifying)
  const activeIdx = progress ? stageIndex(stages, progress.stage) : 0;
  const activeStage = stages[activeIdx];
  const ActiveIcon = activeStage.icon;
  const isLong = elapsedMs >= 15000;

  return (
    <div className="space-y-3 w-full max-w-sm">
      {/* Active stage hero */}
      <div className="flex items-start gap-3 rounded-xl bg-zinc-800/60 border border-zinc-700/40 px-3.5 py-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600/20 border border-blue-600/30 mt-0.5">
          {activeIdx === stages.length - 1 && progress?.stage === "citations"
            ? <ActiveIcon className="h-4 w-4 text-blue-400" />
            : <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
          }
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium text-white">
              {activeStage.label(progress)}
            </p>
            <span className="shrink-0 text-xs font-mono text-zinc-500 tabular-nums">
              {formatElapsed(elapsedMs)}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-zinc-400 leading-relaxed">
            {activeStage.detail}
          </p>
        </div>
      </div>

      {/* Stage list */}
      <div className="space-y-0.5 pl-1">
        {stages.map((stage, idx) => {
          const isDone = idx < activeIdx;
          const isActive = idx === activeIdx;
          const isPending = idx > activeIdx;
          const Icon = stage.icon;

          return (
            <div
              key={stage.key}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 transition-colors",
                isActive && "bg-blue-600/8",
              )}
            >
              <div className={cn(
                "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                isDone   && "bg-green-600/20 text-green-400",
                isActive && "bg-blue-600/20 text-blue-400",
                isPending && "bg-zinc-800 text-zinc-600",
              )}>
                {isDone
                  ? <CheckCircle2 className="h-3.5 w-3.5" />
                  : isActive
                  ? <Loader2 className="h-3 w-3 animate-spin" />
                  : <Icon className="h-3 w-3" />
                }
              </div>
              <span className={cn(
                "text-xs truncate",
                isDone   && "text-zinc-500",
                isActive && "text-blue-300 font-medium",
                isPending && "text-zinc-600",
              )}>
                {stage.label(isActive || isDone ? progress : undefined)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Long-wait reassurance copy */}
      {isLong && (
        <p className="text-center text-xs text-zinc-600 animate-pulse px-1">
          {LONG_WAIT_COPY[longWaitIdx]}
        </p>
      )}
    </div>
  );
}
