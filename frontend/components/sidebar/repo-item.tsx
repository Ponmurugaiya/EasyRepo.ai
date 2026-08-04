"use client";

import { useState } from "react";
import { cn, truncate } from "@/lib/utils";
import { StatusBadge } from "@/components/ui/status-badge";
import { MessageSquare, GitBranch, RefreshCw } from "lucide-react";
import { ingestRepository, getRepositoryStatus, getRepository } from "@/lib/api";
import { useChatStore } from "@/store/chat-store";
import { useGraphStore } from "@/store/graph-store";
import type { RepoSession } from "@/types/chat";

interface RepoItemProps {
  repo: RepoSession;
  active: boolean;
  messageCount: number;
  onClick: () => void;
  showGraphHint?: boolean;
}

export function RepoItem({
  repo,
  active,
  messageCount,
  onClick,
  showGraphHint = false,
}: RepoItemProps) {
  const [reindexing, setReindexing] = useState(false);
  const { updateRepoSession } = useChatStore();
  const { activeRepoId: graphRepoId, refreshGraph } = useGraphStore();

  async function handleReindex(e: React.MouseEvent) {
    e.stopPropagation();
    if (reindexing) return;
    if (
      !confirm(
        `Re-index "${repo.repoName}"?\n\nThis will re-run the full pipeline (clone → extract → embed). Takes 2–5 min.`
      )
    )
      return;

    setReindexing(true);
    try {
      await ingestRepository(repo.repoUrl);
      updateRepoSession(repo.repoId, { status: "indexing" });

      for (let i = 0; i < 300; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const s = await getRepositoryStatus(repo.repoId);
        updateRepoSession(repo.repoId, { status: s.status });
        if (s.status === "ready") {
          const full = await getRepository(repo.repoId);
          updateRepoSession(repo.repoId, {
            entityCount: full.entity_count ?? 0,
            relationshipCount: full.relationship_count ?? 0,
            indexedAt: full.indexed_at,
          });
          if (graphRepoId === repo.repoId) refreshGraph();
          break;
        }
        if (s.status === "failed") break;
      }
    } catch {
      // status badge will show failed
    } finally {
      setReindexing(false);
    }
  }

  return (
    // Use div + role="button" so we can nest a real <button> inside without
    // violating the HTML spec (<button> cannot contain <button>).
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-lg transition-colors group cursor-pointer",
        "hover:bg-white/5 select-none",
        active && "bg-white/10"
      )}
    >
      <div className="flex items-start gap-2.5">
        <GitBranch
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0 transition-colors",
            active ? "text-blue-400" : "text-zinc-500"
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <span
              className={cn(
                "text-sm font-medium truncate",
                active ? "text-white" : "text-zinc-200"
              )}
            >
              {truncate(repo.repoName, 22)}
            </span>
            <div className="flex items-center gap-1 shrink-0">
              <StatusBadge status={reindexing ? "indexing" : repo.status} />
              {/* Re-index button — separate <button> is valid here since
                  the outer element is now a <div>, not a <button> */}
              <button
                type="button"
                onClick={handleReindex}
                disabled={reindexing}
                title="Re-index this repository"
                className={cn(
                  "opacity-0 group-hover:opacity-100 transition-opacity",
                  "p-0.5 rounded text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700",
                  "disabled:opacity-30"
                )}
              >
                <RefreshCw
                  className={cn("h-3 w-3", reindexing && "animate-spin")}
                />
              </button>
            </div>
          </div>

          {showGraphHint ? (
            <div className="mt-0.5 flex items-center gap-1 text-xs text-zinc-500">
              <GitBranch className="h-3 w-3" />
              <span>view graph</span>
            </div>
          ) : messageCount > 0 ? (
            <div className="mt-0.5 flex items-center gap-1 text-xs text-zinc-500">
              <MessageSquare className="h-3 w-3" />
              <span>
                {messageCount} message{messageCount !== 1 ? "s" : ""}
              </span>
            </div>
          ) : null}

          {reindexing && (
            <p className="mt-0.5 text-[10px] text-blue-400 animate-pulse">
              Re-indexing…
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
