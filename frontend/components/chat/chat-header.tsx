"use client";

import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { RotateCcw, PanelLeft, ExternalLink, FileSearch, Zap } from "lucide-react";
import { useChatStore } from "@/store/chat-store";
import { truncate } from "@/lib/utils";
import type { RepoSession } from "@/types/chat";

interface ChatHeaderProps {
  repo: RepoSession;
  onClear: () => void;
}

export function ChatHeader({ repo, onClear }: ChatHeaderProps) {
  const { sidebarOpen, setSidebarOpen } = useChatStore();
  const isGitHub = /github\.com/i.test(repo.repoUrl);

  return (
    <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3 bg-zinc-950/60 backdrop-blur">
      {/* Sidebar toggle (shown when sidebar is closed) */}
      {!sidebarOpen && (
        <Tooltip>
          <TooltipTrigger>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-zinc-400 hover:text-white shrink-0"
              onClick={() => setSidebarOpen(true)}
            >
              <PanelLeft className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
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
        {isGitHub && (
          <Tooltip>
            <TooltipTrigger>
              <a
                href={repo.repoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
                aria-label="View on GitHub"
              >
                <ExternalLink className="h-4 w-4" />
              </a>
            </TooltipTrigger>
            <TooltipContent>View on GitHub</TooltipContent>
          </Tooltip>
        )}

        <Tooltip>
          <TooltipTrigger>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-zinc-400 hover:text-white"
              onClick={onClear}
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Clear conversation</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
