"use client";

import { GitBranch, MessageSquare, Zap, ShieldCheck, Plus, FileSearch } from "lucide-react";
import Image from "next/image";
import { Button } from "../../components/ui/button";
import { useChatStore } from "../../store/chat-store";
import { AddRepoButton } from "../../components/sidebar/add-repo-button";
import { StatusBadge } from "../../components/ui/status-badge";

const FEATURES = [
  {
    icon: GitBranch,
    title: "Graph-aware retrieval",
    desc: "Understands call chains, class hierarchies, and import graphs — not just text similarity.",
  },
  {
    icon: MessageSquare,
    title: "Cited answers",
    desc: "Every answer links back to exact file paths and line numbers from your codebase.",
  },
  {
    icon: Zap,
    title: "Relationship expansion",
    desc: "Automatically pulls in callers, callees, and parent classes for complete context.",
  },
  {
    icon: ShieldCheck,
    title: "Hallucination rate",
    desc: "Each answer shows a citation validation score so you know what to trust.",
  },
];

export function WelcomeScreen() {
  const { repoSessions, setActiveRepo, newChatMode } = useChatStore();
  const repos = Object.values(repoSessions);
  const hasRepos = repos.length > 0;

  return (
    <div className="flex flex-col items-center justify-center h-full px-6 py-12 text-center overflow-y-auto">
      <div className="flex h-20 w-20 items-center justify-center rounded-3xl overflow-hidden mb-6">
        <Image src="/logo.png" alt="EasyRepo logo" width={80} height={80} className="object-contain" />
      </div>

      <h1 className="text-3xl font-bold text-white mb-2">EasyRepo</h1>
      <p className="text-zinc-400 max-w-md mb-8 leading-relaxed">
        AI-powered codebase intelligence. Ask natural-language questions and get
        cited answers grounded in your actual code structure.
      </p>

      {hasRepos ? (
        /* ── Existing repos ── */
        <div className="w-full max-w-lg space-y-3 mb-6">
          <p className="text-sm text-zinc-400 text-left font-medium mb-1">
            Pick a repo to chat with:
          </p>

          {repos.map((repo) => (
            <button
              key={repo.repoId}
              onClick={() => setActiveRepo(repo.repoId, newChatMode)}
              disabled={repo.status !== "ready"}
              className="w-full flex items-center gap-3 rounded-xl border border-zinc-700 bg-zinc-800/50 px-4 py-3 text-left hover:bg-zinc-700/60 hover:border-zinc-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed group"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-zinc-700 group-hover:bg-zinc-600 transition-colors">
                <GitBranch className="h-4 w-4 text-zinc-300" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-zinc-100 truncate">
                    {repo.repoName}
                  </span>
                  <StatusBadge status={repo.status} />
                </div>
                {repo.status === "ready" && (
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-zinc-500">
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
                )}
                {repo.status !== "ready" && (
                  <p className="text-xs text-zinc-500 mt-0.5 capitalize">{repo.status}…</p>
                )}
              </div>
            </button>
          ))}

          {/* Add new repo option */}
          <div className="pt-1">
            <AddRepoButton variant="ghost" />
          </div>
        </div>
      ) : (
        /* ── No repos yet ── */
        <div className="flex flex-col sm:flex-row gap-3 mb-12">
          <AddRepoButton />
        </div>
      )}

      {/* Feature grid — always shown */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl w-full text-left mt-4">
        {FEATURES.map(({ icon: Icon, title, desc }) => (
          <div
            key={title}
            className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"
          >
            <div className="flex items-center gap-2 mb-2">
              <Icon className="h-4 w-4 text-blue-400" />
              <span className="text-sm font-medium text-zinc-200">{title}</span>
            </div>
            <p className="text-xs text-zinc-500 leading-relaxed">{desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
