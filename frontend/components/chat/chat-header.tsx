"use client";

import { StatusBadge } from "@/components/ui/status-badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { RotateCcw, PanelLeft, ExternalLink, FileSearch, Zap } from "lucide-react";
import { useChatStore } from "@/store/chat-store";
import { truncate, cn } from "@/lib/utils";
import type { RepoSession } from "@/types/chat";

interface ChatHeaderProps {
  repo: RepoSession;
  onClear: () => void;
}

const iconBtn = cn(
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
  "text-zinc-400 hover:text-white hover:bg-white/5 transition-colors",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
);

export function ChatHeader({ repo, onClear }: ChatHeaderProps) {
  const { sidebarOpen, setSidebarOpen } = useChatStore();
  const isGitHub = /github\.com/i.test(repo.repoUrl);

  return (
    <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3 bg-zinc-950/60 backdrop-blur">

      {/* Sidebar toggle */}
      {!sidebarOpen && (
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                className={iconBtn}
                onClick={() => setSidebarOpen(true)}
                aria-label="Open sidebar"
              >
                <PanelLeft className="h-4 w-4" />
              </button>
            }
          />
          <TooltipContent>Open sidebar</TooltipContent>
        </Tooltip>
      )}

      {/* Repo name + status */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm text-white truncate">
            {truncate(repo.repoName, 40)}
          </span>
          <StatusBadge status={repo.status} />
        </div>
        <div className="flex items-center gap-3 mt-0.5 text-xs text-zinc-600">
          {repo.entityCount > 0 && (
            <span className="flex items-center gap-1">
              <FileSearch className="h-3 w-3" />
              {repo.entityCount.toLocaleString()} entities
            </span>
          )}
          {repo.relationshipCount > 0 && (
            <span className="flex items-center gap-1">
              <Zap className="h-3 w-3" />
              {repo.relationshipCount.toLocaleString()} relationships
            </span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0">

        {/* GitHub link */}
        {isGitHub && (
          <Tooltip>
            <TooltipTrigger
              render={
                <a
                  href={repo.repoUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={iconBtn}
                  aria-label="View on GitHub"
                >
                  <ExternalLink className="h-4 w-4" />
                </a>
              }
            />
            <TooltipContent>View on GitHub</TooltipContent>
          </Tooltip>
        )}

        {/* Clear conversation */}
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                className={iconBtn}
                onClick={onClear}
                aria-label="Clear conversation"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            }
          />
          <TooltipContent>Clear conversation</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
