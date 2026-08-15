"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Spinner } from "../../components/ui/spinner";
import {
  Plus, GitBranch, CheckCircle2, XCircle,
  Download, FileSearch, GitMerge, Cpu, Database, Check, Loader2, AlertTriangle,
} from "lucide-react";
import { ingestRepository, getRepositoryStatus } from "../../lib/api";
import { useChatStore } from "../../store/chat-store";
import { cn, sleep } from "../../lib/utils";
import { parseProgress } from "../../lib/progress";
import type { RepoStatus } from "../../types/api";

// ─── Pipeline stage definitions ──────────────────────────────────────────────
// Each stage has:
//   keyword   — matched against the backend progress_message
//   label     — short title shown in the list
//   detail()  — longer sentence shown as the active description
//   Icon      — lucide icon
//   tipTime   — rough time hint shown while waiting

interface Stage {
  keyword: string;
  label: string;
  detail: (msg: string) => string;
  Icon: React.ElementType;
  tipTime: string;
}

const STAGES: Stage[] = [
  {
    keyword: "cloning",
    label: "Cloning repository",
    detail: () => "Fetching source code from GitHub…",
    Icon: Download,
    tipTime: "~5 sec",
  },
  {
    keyword: "parsing",
    label: "Parsing source files",
    detail: () =>
      "Reading every Python & TypeScript file with Tree-sitter to extract functions, classes, and methods.",
    Icon: FileSearch,
    tipTime: "~10 sec",
  },
  {
    keyword: "resolving",
    label: "Resolving relationships",
    detail: (msg) => {
      const m = msg.match(/(\d+)\s+entit/i);
      const n = m ? m[1] : "";
      return `Building the call graph — tracing CALLS, IMPORTS, INHERITS, and IMPLEMENTS edges${n ? ` across ${n} entities` : ""}.`;
    },
    Icon: GitMerge,
    tipTime: "~5 sec",
  },
  {
    keyword: "embedding",
    label: "Generating embeddings",
    detail: (msg) => {
      // "Embedding 256/412 entities…" or "Generating embeddings for 412 entities via Voyage AI…"
      const progress = msg.match(/(\d+)\/(\d+)/);
      if (progress) {
        const done = parseInt(progress[1]);
        const total = parseInt(progress[2]);
        const pct = Math.round((done / total) * 100);
        return `Converting code into semantic vectors via Voyage AI — ${done} of ${total} entities (${pct}%).`;
      }
      const total = msg.match(/for\s+(\d+)\s+entit/i);
      return `Sending code to Voyage AI to generate semantic search vectors${total ? ` for ${total[1]} entities` : ""}. This is the longest step.`;
    },
    Icon: Cpu,
    tipTime: "1–3 min",
  },
  {
    keyword: "saving",
    label: "Saving to database",
    detail: () =>
      "Storing all entities, relationships, and embeddings in Supabase with pgvector indexes.",
    Icon: Database,
    tipTime: "~5 sec",
  },
];

const STAGE_ORDER = STAGES.map((s) => s.keyword);

function getCurrentStageIdx(msg: string): number {
  const lower = msg.toLowerCase();
  return STAGE_ORDER.findIndex((k) => lower.includes(k));
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

// ─── Component ────────────────────────────────────────────────────────────────

type Step = "idle" | "ingesting" | "polling" | "done" | "error";

export function AddRepoButton({ variant = "outline" }: { variant?: "outline" | "ghost" }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState("");
  const [step, setStep] = useState<Step>("idle");
  const [status, setStatus] = useState<RepoStatus>("pending");
  const [progressMessage, setProgressMessage] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState("");
  const [elapsedSec, setElapsedSec] = useState(0);
  const [entityCount, setEntityCount] = useState<number | null>(null);
  const [relCount, setRelCount] = useState<number | null>(null);
  const [addedRepoId, setAddedRepoId] = useState<string | null>(null);
  const [languageWarning, setLanguageWarning] = useState<string | null>(null);

  const abortRef = useRef(false);
  const startTimeRef = useRef<number>(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { addRepoSession, updateRepoSession, setActiveRepo } = useChatStore();

  // Elapsed timer — starts when polling begins, stops when done/error
  useEffect(() => {
    if (step === "polling" || step === "ingesting") {
      if (!startTimeRef.current) startTimeRef.current = Date.now();
      timerRef.current = setInterval(() => {
        setElapsedSec(Math.floor((Date.now() - startTimeRef.current) / 1000));
      }, 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [step]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!source.trim()) return;

    abortRef.current = false;
    startTimeRef.current = Date.now();
    setElapsedSec(0);
    setEntityCount(null);
    setRelCount(null);
    setLanguageWarning(null);
    setStep("ingesting");
    setErrorMsg("");
    setProgressMessage("Submitting repository…");

    try {
      const repo = await ingestRepository(source.trim());
      addRepoSession(repo);
      setAddedRepoId(repo.repo_id);

      if (repo.status === "ready") {
        setStatus("ready");
        setEntityCount(repo.entity_count);
        setRelCount(repo.relationship_count);
        if (repo.language_warning) setLanguageWarning(repo.language_warning);
        setStep("done");
        return;
      }

      setStep("polling");
      setStatus(repo.status);
      // Start with an optimistic "starting" message immediately
      setProgressMessage("Cloning repository…|pct=2");

      for (let i = 0; i < 300; i++) {
        if (abortRef.current) break;
        await sleep(1000);

        const statusResp = await getRepositoryStatus(repo.repo_id);
        setStatus(statusResp.status);
        // Only update the message if backend has a real one — otherwise
        // keep the last known message so we never go back to "Queued"
        if (statusResp.progress_message) {
          setProgressMessage(statusResp.progress_message);
        }
        // Capture language warning as soon as the backend sends it
        if (statusResp.language_warning) {
          setLanguageWarning(statusResp.language_warning);
        }
        updateRepoSession(repo.repo_id, {
          status: statusResp.status,
          indexedAt: statusResp.indexed_at,
        });

        if (statusResp.status === "ready") {
          // For fast repos that complete before we see real intermediate
          // progress messages, animate through all stages visually.
          const { message } = parseProgress(progressMessage);
          const currentStageIdx = getCurrentStageIdx(message);
          // If we haven't progressed past cloning (idx 0), replay all stages
          if (currentStageIdx <= 0) {
            const replayStages = [
              { msg: "Cloning repository…|pct=5",                              delay: 250 },
              { msg: "Parsing source files…|pct=15",                          delay: 350 },
              { msg: "Resolving relationships for entities…|pct=30",          delay: 300 },
              { msg: "Generating embeddings for entities via Voyage AI…|pct=55", delay: 400 },
              { msg: "Embedding entities…|pct=88",                            delay: 300 },
              { msg: "Saving to database…|pct=95",                            delay: 250 },
            ];
            for (const s of replayStages) {
              if (abortRef.current) break;
              setProgressMessage(s.msg);
              await sleep(s.delay);
            }
          }

          const { getRepository } = await import("../../lib/api");
          const full = await getRepository(repo.repo_id);
          updateRepoSession(repo.repo_id, {
            entityCount: full.entity_count,
            relationshipCount: full.relationship_count,
          });
          setEntityCount(full.entity_count);
          setRelCount(full.relationship_count);
          if (full.language_warning) setLanguageWarning(full.language_warning);
          setStep("done");
          return;
        }
        if (statusResp.status === "failed") {
          throw new Error(
            statusResp.progress_message?.replace(/^failed:\s*/i, "") ??
              "Indexing failed on the server."
          );
        }
      }
      throw new Error("Timed out waiting for indexing to complete.");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unexpected error";
      setErrorMsg(msg);
      setStep("error");
    }
  }

  function handleOpenChange(val: boolean) {
    if (!val) {
      abortRef.current = true;
      if (step !== "polling") {
        setStep("idle");
        setSource("");
        setErrorMsg("");
        setProgressMessage("");
        setAddedRepoId(null);
        setLanguageWarning(null);
        startTimeRef.current = 0;
      }
    }
    setOpen(val);
  }

  function handleDone() {
    if (addedRepoId) setActiveRepo(addedRepoId);
    setOpen(false);
    setStep("idle");
    setSource("");
    setProgressMessage("");
    setAddedRepoId(null);
    setLanguageWarning(null);
    startTimeRef.current = 0;
  }

  const isLoading = step === "ingesting" || step === "polling";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger
        render={
          <Button
            variant={variant}
            size="sm"
            className="w-full gap-2 border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700 hover:text-white"
          >
            <Plus className="h-4 w-4" />
            Add new repository
          </Button>
        }
      />

      <DialogContent className="sm:max-w-lg bg-zinc-900 border-zinc-700 text-white">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-blue-400" />
            Add a repository
          </DialogTitle>
          <DialogDescription className="text-zinc-400">
            Paste a GitHub URL or a local path. EasyRepo indexes the code
            structure and makes it queryable with cited answers.
          </DialogDescription>
        </DialogHeader>

        {/* ── Idle / error ── */}
        {(step === "idle" || step === "error") && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="source" className="text-zinc-300">
                Repository URL or path
              </Label>
              <Input
                id="source"
                value={source}
                onChange={(e) => setSource(e.target.value)}
                placeholder="https://github.com/owner/repo"
                className="bg-zinc-800 border-zinc-600 text-white placeholder:text-zinc-500 focus-visible:ring-blue-500"
                autoFocus
                disabled={isLoading}
              />
              {step === "error" && (
                <p className="flex items-start gap-1.5 text-sm text-red-400">
                  <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  {errorMsg}
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                type="submit"
                disabled={!source.trim() || isLoading}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white"
              >
                Index repository
              </Button>
            </DialogFooter>
          </form>
        )}

        {/* ── Submitting ── */}
        {step === "ingesting" && (
          <div className="flex items-center gap-3 py-4 px-1">
            <Spinner size="sm" className="text-blue-400 shrink-0" />
            <p className="text-sm text-zinc-300">
              Submitting to the indexing queue…
            </p>
          </div>
        )}

        {/* ── Polling — main progress view ── */}
        {step === "polling" && (
          <div className="space-y-4 py-2">
            {(() => {
              const { message, pct } = parseProgress(progressMessage);
              const overallPct = pct ?? 0;
              const activeStageIdx = getCurrentStageIdx(message);
              const activeStage = activeStageIdx >= 0 ? STAGES[activeStageIdx] : null;

              // Per-stage percentage: within embedding we can show finer progress
              const stageProgress = (() => {
                if (!activeStage) return null;
                if (activeStage.keyword === "embedding" && pct !== null) {
                  // Map overall 35–90 back to 0–100 within the stage
                  const stagePct = Math.round(((pct - 35) / 55) * 100);
                  return Math.min(100, Math.max(0, stagePct));
                }
                return null;
              })();

              return (
                <>
                  {/* Overall progress bar */}
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-zinc-400 font-medium">Overall progress</span>
                      <div className="flex items-center gap-2">
                        <span className="text-blue-400 font-mono font-semibold tabular-nums">
                          {overallPct}%
                        </span>
                        <span className="text-zinc-600 tabular-nums">
                          {formatElapsed(elapsedSec)}
                        </span>
                      </div>
                    </div>
                    <div className="h-2 w-full rounded-full bg-zinc-800 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all duration-700 ease-out"
                        style={{ width: `${overallPct}%` }}
                      />
                    </div>
                  </div>

                  {/* Active stage hero card */}
                  <div className="rounded-xl bg-zinc-800/60 border border-zinc-700/50 p-3.5">
                    <div className="flex items-start gap-3">
                      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-600/20 border border-blue-600/30">
                        {activeStage
                          ? <activeStage.Icon className="h-4 w-4 text-blue-400" />
                          : <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
                        }
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold text-white">
                          {activeStage?.label ?? "Processing…"}
                        </p>
                        <p className="mt-0.5 text-xs text-zinc-400 leading-relaxed">
                          {activeStage
                            ? activeStage.detail(message)
                            : message || "Waiting for the worker…"
                          }
                        </p>
                        {/* Per-stage progress bar (only for embedding) */}
                        {stageProgress !== null && (
                          <div className="mt-2 space-y-1">
                            <div className="flex justify-between text-[10px] text-zinc-500">
                              <span>Embedding progress</span>
                              <span className="font-mono text-blue-400">{stageProgress}%</span>
                            </div>
                            <div className="h-1.5 w-full rounded-full bg-zinc-700 overflow-hidden">
                              <div
                                className="h-full rounded-full bg-blue-500 transition-all duration-500 ease-out"
                                style={{ width: `${stageProgress}%` }}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Stage pipeline list */}
                  <div className="space-y-0.5">
                    {STAGES.map((stage, idx) => {
                      const isActive = idx === activeStageIdx;
                      const isDone = activeStageIdx > idx ||
                        (activeStageIdx === -1 && status === "ready");
                      const isPending = !isActive && !isDone;

                      // Per-stage percentage shown on each row
                      const rowPct = isDone
                        ? 100
                        : isActive
                        ? (stageProgress !== null ? stageProgress : null)
                        : null;

                      return (
                        <div
                          key={stage.keyword}
                          className={cn(
                            "flex items-center gap-3 rounded-lg px-3 py-2 transition-colors",
                            isActive && "bg-blue-600/10 border border-blue-600/20",
                          )}
                        >
                          {/* State icon */}
                          <div className={cn(
                            "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px]",
                            isActive && "bg-blue-600 text-white",
                            isDone && "bg-green-600/20 text-green-400",
                            isPending && "bg-zinc-700 text-zinc-500",
                          )}>
                            {isDone
                              ? <Check className="h-3 w-3" />
                              : isActive
                              ? <Loader2 className="h-3 w-3 animate-spin" />
                              : <span>{idx + 1}</span>
                            }
                          </div>

                          {/* Label + mini bar */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <span className={cn(
                                "text-xs truncate",
                                isActive && "text-blue-300 font-medium",
                                isDone && "text-zinc-500",
                                isPending && "text-zinc-600",
                              )}>
                                {stage.label}
                              </span>
                              <span className={cn(
                                "shrink-0 text-[10px] font-mono tabular-nums",
                                isDone && "text-green-500",
                                isActive && "text-blue-400",
                                isPending && "text-zinc-600",
                              )}>
                                {isDone
                                  ? "100%"
                                  : isActive && rowPct !== null
                                  ? `${rowPct}%`
                                  : isPending
                                  ? stage.tipTime
                                  : ""}
                              </span>
                            </div>
                            {/* Mini progress bar per row */}
                            {(isActive || isDone) && (
                              <div className="mt-1 h-1 w-full rounded-full bg-zinc-700/50 overflow-hidden">
                                <div
                                  className={cn(
                                    "h-full rounded-full transition-all duration-500",
                                    isDone ? "bg-green-500" : "bg-blue-500",
                                  )}
                                  style={{
                                    width: `${isDone ? 100 : (rowPct ?? 50)}%`,
                                  }}
                                />
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <p className="text-center text-xs text-zinc-600 pb-1">
                    {activeStageIdx === 3
                      ? "Embedding is the slowest step — Voyage AI is generating vectors"
                      : "Usually completes in 2–5 minutes for medium repos"}
                  </p>
                </>
              );
            })()}
          </div>
        )}

        {/* ── Done ── */}
        {step === "done" && (
          <div className="flex flex-col items-center gap-4 py-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-green-600/20 border border-green-600/30">
              <CheckCircle2 className="h-7 w-7 text-green-400" />
            </div>
            <div className="text-center space-y-1">
              <p className="font-semibold text-white">Repository indexed</p>
              <p className="text-sm text-zinc-400">
                Completed in {formatElapsed(elapsedSec)}
              </p>
              {(entityCount !== null || relCount !== null) && (
                <div className="flex items-center justify-center gap-4 mt-2 text-xs text-zinc-500">
                  {entityCount !== null && (
                    <span>{entityCount.toLocaleString()} entities</span>
                  )}
                  {relCount !== null && (
                    <span>{relCount.toLocaleString()} relationships</span>
                  )}
                </div>
              )}
            </div>
            {/* Language warning banner */}
            {languageWarning && (
              <div className="w-full rounded-lg bg-amber-950/40 border border-amber-700/50 px-3.5 py-3 flex items-start gap-2.5">
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-400" />
                <p className="text-xs text-amber-300 leading-relaxed">
                  {languageWarning}
                </p>
              </div>
            )}
            <Button
              onClick={handleDone}
              className="bg-blue-600 hover:bg-blue-500 text-white"
            >
              Start chatting
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
