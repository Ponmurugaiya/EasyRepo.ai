"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat-store";
import { useGraphStore } from "@/store/graph-store";
import { useAuthStore } from "@/store/auth-store";
import { RepoItem } from "./repo-item";
import { DevLoginModal } from "@/components/auth/dev-login-modal";
import { Bot, ChevronLeft, MessageSquare, GitBranch, KeyRound, SquarePen } from "lucide-react";
import { Button } from "@/components/ui/button";

type SidebarTab = "chat" | "graph";

export function Sidebar() {
  const {
    repoSessions,
    conversations,
    activeRepoId,
    sidebarOpen,
    setSidebarOpen,
    setActiveRepo,
    startNewChat,
  } = useChatStore();

  const {
    isOpen: graphOpen,
    activeRepoId: graphRepoId,
    openGraph,
    closeGraph,
  } = useGraphStore();

  const { isLoggedIn } = useAuthStore();
  const [loginOpen, setLoginOpen] = useState(false);

  // Active tab: if graph panel is open, show graph tab as active
  const activeTab: SidebarTab = graphOpen ? "graph" : "chat";

  const repos = Object.values(repoSessions);

  function handleTabChange(tab: SidebarTab) {
    if (tab === "chat") {
      closeGraph();
    } else {
      // Switching to graph tab — if a repo is active, open its graph
      if (activeRepoId) {
        openGraph(activeRepoId);
      }
    }
  }

  function handleRepoClickInChat(repoId: string) {
    closeGraph();
    setActiveRepo(repoId);
    setSidebarOpen(false);
  }

  function handleRepoClickInGraph(repoId: string) {
    setActiveRepo(repoId);
    openGraph(repoId);
    setSidebarOpen(false);
  }

  return (
    <>
      {/* Mobile overlay */}
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
        {/* ── Branding header + New Chat ── */}
        <div className="flex items-center justify-between px-4 py-4 border-b border-zinc-800">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <span className="text-sm font-semibold text-white">EasyRepo</span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-zinc-400 hover:text-white"
              onClick={() => {
                closeGraph();
                startNewChat();
                setSidebarOpen(false);
              }}
              aria-label="New chat"
              title="New chat"
            >
              <SquarePen className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-zinc-400 hover:text-white"
              onClick={() => setSidebarOpen(false)}
              aria-label="Close sidebar"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* ── Tab switcher ── */}
        <div className="flex border-b border-zinc-800">
          <button
            onClick={() => handleTabChange("chat")}
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium",
              "transition-colors border-b-2",
              activeTab === "chat"
                ? "text-white border-blue-500"
                : "text-zinc-500 border-transparent hover:text-zinc-300"
            )}
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Chat
          </button>
          <button
            onClick={() => handleTabChange("graph")}
            className={cn(
              "flex-1 flex items-center justify-center gap-1.5 py-2.5 text-xs font-medium",
              "transition-colors border-b-2",
              activeTab === "graph"
                ? "text-white border-blue-500"
                : "text-zinc-500 border-transparent hover:text-zinc-300"
            )}
          >
            <GitBranch className="h-3.5 w-3.5" />
            Graph
          </button>
        </div>

        {/* ── Repo list ── */}
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
          {repos.length === 0 ? (
            <div className="px-3 py-8 text-center text-xs text-zinc-500">
              <p>No repos yet.</p>
              <p className="mt-1">Add a GitHub URL to get started.</p>
            </div>
          ) : (
            repos.map((repo) => {
              const conv = conversations[repo.repoId];
              const messageCount =
                conv?.messages.filter((m) => m.role === "user").length ?? 0;

              const isActive =
                activeTab === "chat"
                  ? repo.repoId === activeRepoId && !graphOpen
                  : repo.repoId === graphRepoId && graphOpen;

              return (
                <RepoItem
                  key={repo.repoId}
                  repo={repo}
                  active={isActive}
                  messageCount={activeTab === "chat" ? messageCount : 0}
                  onClick={() =>
                    activeTab === "chat"
                      ? handleRepoClickInChat(repo.repoId)
                      : handleRepoClickInGraph(repo.repoId)
                  }
                  showGraphHint={activeTab === "graph"}
                />
              );
            })
          )}
        </div>

        {/* ── Tab hint + Dev login ── */}
        <div className="px-4 py-3 border-t border-zinc-800 space-y-2">
          <p className="text-xs text-zinc-600">
            {activeTab === "chat"
              ? "Repos saved locally · no login required"
              : "Click a repo to view its code graph"}
          </p>
          <button
            onClick={() => setLoginOpen(true)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors",
              isLoggedIn
                ? "text-green-400 hover:text-green-300 hover:bg-green-900/20"
                : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800"
            )}
          >
            <KeyRound className="h-3.5 w-3.5 shrink-0" />
            {isLoggedIn ? "Dev logged in · persistent history" : "Dev login"}
          </button>
        </div>

        <DevLoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
      </aside>
    </>
  );
}
