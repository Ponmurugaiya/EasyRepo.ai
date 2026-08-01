"use client";

import { Bot, GitBranch, Zap, FileSearch } from "lucide-react";
import type { RepoSession } from "@/types/chat";

const SUGGESTED_QUESTIONS = [
  "How does authentication work in this codebase?",
  "Walk me through the main data flow",
  "What are the key classes and their relationships?",
  "Where are the API endpoints defined?",
];

interface EmptyStateProps {
  repo: RepoSession;
  onSuggest: (q: string) => void;
}

export function EmptyState({ repo, onSuggest }: EmptyStateProps) {
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
      <p className="text-sm text-zinc-500 mb-8 max-w-sm">
        Ask anything about the codebase — architecture, how specific features
        work, where code is defined, call chains, and more.
      </p>

      {/* Suggested questions */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button
            key={q}
            onClick={() => onSuggest(q)}
            className="rounded-xl border border-zinc-700 bg-zinc-800/50 px-4 py-3 text-left text-sm text-zinc-300 hover:bg-zinc-700/60 hover:text-white hover:border-zinc-600 transition-colors"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
