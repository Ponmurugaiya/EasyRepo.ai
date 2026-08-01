"use client";

import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat-store";
import { RepoItem } from "./repo-item";
import { AddRepoButton } from "./add-repo-button";
import { Bot, ChevronLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Sidebar() {
  const {
    repoSessions,
    conversations,
    activeRepoId,
    sidebarOpen,
    setSidebarOpen,
    setActiveRepo,
  } = useChatStore();

  const repos = Object.values(repoSessions);

  return (
    <>
      {/* Overlay for mobile */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed md:relative z-30 flex h-full flex-col",
          "w-64 bg-zinc-900 border-r border-zinc-800 transition-transform duration-200",
          sidebarOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
          !sidebarOpen && "md:w-0 md:overflow-hidden md:border-0"
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <span className="text-sm font-semibold text-white">EasyRepo</span>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-zinc-400 hover:text-white md:flex"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close sidebar"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
        </div>

        {/* Add repo */}
        <div className="px-3 py-3 border-b border-zinc-800">
          <AddRepoButton />
        </div>

        {/* Repo list */}
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
          {repos.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-zinc-500">
              <p>No repos yet.</p>
              <p className="mt-1">Add a GitHub URL to get started.</p>
            </div>
          ) : (
            repos.map((repo) => {
              const conv = conversations[repo.repoId];
              const messageCount = conv?.messages.filter(
                (m) => m.role === "user"
              ).length ?? 0;
              return (
                <RepoItem
                  key={repo.repoId}
                  repo={repo}
                  active={repo.repoId === activeRepoId}
                  messageCount={messageCount}
                  onClick={() => {
                    setActiveRepo(repo.repoId);
                    setSidebarOpen(false);
                  }}
                />
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-zinc-800">
          <p className="text-xs text-zinc-600">
            Temporary session · no login required
          </p>
        </div>
      </aside>
    </>
  );
}
