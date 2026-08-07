"use client";

import { Bot, GitBranch, Zap, FileSearch } from "lucide-react";
import type { RepoSession } from "@/types/chat";

interface EmptyStateProps {
  repo: RepoSession;
  onSuggest: (q: string) => void;
}

export function EmptyState({ repo }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-12 text-center">
      {/* Icon */}
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-600/20 border border-blue-600/30 mb-5">
        <Bot className="h-8 w-8 text-blue-400" />
      </div>

      {/* Repo info */}
      <div className="mb-2">
        <div className="flex items-center justify-center gap-1.5 text-zinc-400 text-sm mb-1">
          <GitBranch className="h-4 w-4" />
          <span className="font-medium text-zinc-200">{repo.repoName}</span>
        </div>
        <div className="flex items-center justify-center gap-4 text-xs text-zinc-600">
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

      <h2 className="text-xl font-semibold text-white mb-1">
        What do you want to know?
      </h2>
      <p className="text-sm text-zinc-500 max-w-sm">
        Ask anything about the codebase — architecture, how specific features
        work, where code is defined, call chains, and more.
      </p>
    </div>
  );
}
