"use client";

import { useEffect, useState } from "react";
import { cn } from "../../lib/utils";
import {
  Search, GitBranch, Cpu, CheckCircle2, Loader2,
} from "lucide-react";

// ── Pipeline stages ───────────────────────────────────────────────────────────
// Timings are conservative estimates based on real backend logs.
// The component advances through them on a timer; if the response arrives
// early the parent simply unmounts this component.

interface Stage {
  icon: React.ElementType;
  label: string;
  detail: string;
  minMs: number;   // advance to next stage after at least this many ms
}

const STAGES: Stage[] = [
  {
    icon: Search,
    label: "Searching the code graph",
    detail: "Running semantic search across entities and embeddings…",
    minMs: 3000,
  },
  {
    icon: GitBranch,
    label: "Tracing relationships",
    detail: "Expanding call chains, inheritance, and containment edges…",
    minMs: 5000,
  },
  {
    icon: Cpu,
    label: "Generating answer",
    detail: "Sending context to the language model — this is the longest step…",
    minMs: 12000,
  },
  {
    icon: CheckCircle2,
    label: "Validating citations",
    detail: "Verifying every file and line reference against the code graph…",
    minMs: Infinity,  // stays here until response arrives
  },
];

// Reassurance copy shown below the stage list when the wait is long
const LONG_WAIT_COPY = [
  "Complex queries with deep call chains take a moment…",
  "Hang tight — tracing across large repos takes a bit longer…",
  "Still working — the model is reading a lot of context…",
  "Almost there — validating all the citations now…",
];

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

export function ThinkingIndicator() {
  const [stageIdx, setStageIdx] = useState(0);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [longWaitIdx, setLongWaitIdx] = useState(0);

  // Elapsed timer — ticks every second
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsedMs(Date.now() - start), 1000);
    return () => clearInterval(id);
  }, []);

  // Stage advancement — each stage has a minimum duration before advancing
  useEffect(() => {
    if (stageIdx >= STAGES.length - 1) return;
    const minMs = STAGES[stageIdx].minMs;
    const id = setTimeout(() => setStageIdx((i) => i + 1), minMs);
    return () => clearTimeout(id);
  }, [stageIdx]);

  // Rotate reassurance copy every 8s once we've been waiting > 15s
  useEffect(() => {
    if (elapsedMs < 15000) return;
    const id = setInterval(
      () => setLongWaitIdx((i) => (i + 1) % LONG_WAIT_COPY.length),
      8000,
    );
    return () => clearInterval(id);
  }, [elapsedMs >= 15000]); // eslint-disable-line react-hooks/exhaustive-deps

  const activeStage = STAGES[stageIdx];
  const ActiveIcon = activeStage.icon;
  const isLong = elapsedMs >= 15000;

  return (
    <div className="space-y-3 w-full max-w-sm">
      {/* Active stage hero */}
      <div className="flex items-start gap-3 rounded-xl bg-zinc-800/60 border border-zinc-700/40 px-3.5 py-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600/20 border border-blue-600/30 mt-0.5">
          {stageIdx === STAGES.length - 1
            ? <ActiveIcon className="h-4 w-4 text-blue-400" />
            : <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
          }
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-medium text-white">{activeStage.label}</p>
            <span className="shrink-0 text-xs font-mono text-zinc-500 tabular-nums">
              {formatElapsed(elapsedMs)}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-zinc-400 leading-relaxed">
            {activeStage.detail}
          </p>
        </div>
      </div>

      {/* Stage pipeline list */}
      <div className="space-y-0.5 pl-1">
        {STAGES.map((stage, idx) => {
          const isDone = idx < stageIdx;
          const isActive = idx === stageIdx;
          const isPending = idx > stageIdx;
          const Icon = stage.icon;

          return (
            <div
              key={stage.label}
              className={cn(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 transition-colors",
                isActive && "bg-blue-600/8",
              )}
            >
              <div className={cn(
                "flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
                isDone  && "bg-green-600/20 text-green-400",
                isActive  && "bg-blue-600/20 text-blue-400",
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
                isActive  && "text-blue-300 font-medium",
                isPending && "text-zinc-600",
              )}>
                {stage.label}
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
