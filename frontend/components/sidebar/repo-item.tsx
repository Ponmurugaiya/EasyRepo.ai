"use client";

import { cn, truncate } from "@/lib/utils";
import { StatusBadge } from "@/components/ui/status-badge";
import { MessageSquare, GitBranch } from "lucide-react";
import type { RepoSession } from "@/types/chat";

interface RepoItemProps {
  repo: RepoSession;
  active: boolean;
  messageCount: number;
  onClick: () => void;
}

export function RepoItem({ repo, active, messageCount, onClick }: RepoItemProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-lg transition-colors group",
        "hover:bg-white/5",
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
              {truncate(repo.repoName, 24)}
            </span>
            <StatusBadge status={repo.status} />
          </div>
          {messageCount > 0 && (
            <div className="mt-0.5 flex items-center gap-1 text-xs text-zinc-500">
              <MessageSquare className="h-3 w-3" />
              <span>{messageCount} message{messageCount !== 1 ? "s" : ""}</span>
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
