"use client";

import { useState, useEffect } from "react";
import { cn } from "../../lib/utils";
import { useChatStore } from "../../store/chat-store";
import { useGraphStore } from "../../store/graph-store";
import { useAuthStore } from "../../store/auth-store";
import { RepoItem } from "./repo-item";
import { ConversationItem } from "./conversation-item";
import { DevLoginModal } from "../../components/auth/dev-login-modal";
import { CognitoLoginModal } from "../../components/auth/cognito-login-modal";
import { ChevronLeft, MessageSquare, GitBranch, LogIn, LogOut, User, SquarePen } from "lucide-react";
import Image from "next/image";
import { Button } from "../../components/ui/button";

type SidebarTab = "chat" | "graph";

export function Sidebar() {
  const {
    repoSessions,
    conversationList,
    activeConversationId,
    activeRepoId,
    sidebarOpen,
    setSidebarOpen,
    setActiveRepo,
    openConversation,
    startNewChat,
    syncRepoSessions,
  } = useChatStore();

  const {
    isOpen: graphOpen,
    activeRepoId: graphRepoId,
    openGraph,
    closeGraph,
  } = useGraphStore();

  const { isLoggedIn, loginMode, cognitoUser, clearToken } = useAuthStore();
  const [loginOpen, setLoginOpen] = useState(false);
  const [graphNoRepoHint, setGraphNoRepoHint] = useState(false);
  const [selectedTab, setSelectedTab] = useState<SidebarTab>("chat");

  // If the graph panel is closed externally (X button), snap tab back to chat
  useEffect(() => {
    if (!graphOpen && selectedTab === "graph") {
      setSelectedTab("chat");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphOpen]);

  const activeTab = selectedTab;

  const repos = Object.values(repoSessions);

  // Conversations sorted newest-first for the history list
  const sortedConversations = Object.values(conversationList)
    .sort((a, b) => b.updatedAt - a.updatedAt);

  function handleTabChange(tab: SidebarTab) {
    setSelectedTab(tab);
    if (tab === "chat") {
      setGraphNoRepoHint(false);
      closeGraph();
    } else {
      if (repos.length === 0) {
        setGraphNoRepoHint(true);
        return;
      }
      setGraphNoRepoHint(false);
      // Switching to graph tab — if a repo is active, open its graph
      if (activeRepoId) {
        openGraph(activeRepoId);
      }
    }
  }

  function handleRepoClickInGraph(repoId: string) {
    setActiveRepo(repoId);
    openGraph(repoId);
    setSidebarOpen(false);
  }

  async function handleSignOut() {
    if (loginMode === "cognito") {
      try {
        const { configureCognito, signOutCognito } = await import("../../lib/cognito");
        configureCognito();
        await signOutCognito();
      } catch {
        // best-effort
      }
    }
    // Clear all auth and session state immediately — don't wait for the network
    clearToken();
    closeGraph();
    // Wipe repos, conversations and active repo from the store so the
    // signed-out user never sees the previous user's data
    syncRepoSessions([]);
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
            <div className="flex h-7 w-7 items-center justify-center rounded-md overflow-hidden">
              <Image src="/logo.png" alt="EasyRepo logo" width={28} height={28} className="object-contain" />
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

        {/* ── Content: conversation history (chat) or repo list (graph) ── */}
        <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
          {activeTab === "chat" ? (
            /* ── Chat tab: conversation history ── */
            sortedConversations.length === 0 ? (
              <div className="px-3 py-8 text-center text-xs text-zinc-500">
                <MessageSquare className="h-6 w-6 text-zinc-600 mx-auto mb-3" />
                <p>No conversations yet.</p>
                <p className="mt-1">Pick a repo to start chatting.</p>
              </div>
            ) : (
              sortedConversations.map((conv) => (
                <ConversationItem
                  key={conv.id}
                  conversation={conv}
                  active={conv.id === activeConversationId}
                  onClick={() => {
                    openConversation(conv.id);
                    setSidebarOpen(false);
                    closeGraph();
                    setSelectedTab("chat");
                  }}
                />
              ))
            )
          ) : (
            /* ── Graph tab: repo list ── */
            repos.length === 0 ? (
              <div className="px-3 py-8 text-center text-xs text-zinc-500">
                {graphNoRepoHint ? (
                  <>
                    <GitBranch className="h-6 w-6 text-zinc-600 mx-auto mb-3" />
                    <p className="text-zinc-300 font-medium mb-1">No repos yet</p>
                    <p className="text-zinc-500">Add a GitHub repo first to explore its code graph.</p>
                  </>
                ) : (
                  <>
                    <p>No repos yet.</p>
                    <p className="mt-1">Add a GitHub URL to get started.</p>
                  </>
                )}
              </div>
            ) : (
              repos.map((repo) => {
                const isActive =
                  repo.repoId === (graphRepoId ?? activeRepoId) && graphOpen;

                return (
                  <RepoItem
                    key={repo.repoId}
                    repo={repo}
                    active={isActive}
                    messageCount={0}
                    onClick={() => handleRepoClickInGraph(repo.repoId)}
                    showGraphHint
                  />
                );
              })
            )
          )}
        </div>

        {/* ── Tab hint + Login ── */}
        <div className="px-4 py-3 border-t border-zinc-800 space-y-2">
          <p className="text-xs text-zinc-600">
            {activeTab === "chat"
              ? isLoggedIn
                ? "Chat history is saved permanently"
                : "Temporary session · sign in to save history"
              : "Click a repo to view its code graph"}
          </p>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setLoginOpen(true)}
              className={cn(
                "flex flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-xs transition-colors min-w-0",
                isLoggedIn
                  ? "text-green-400 hover:text-green-300 hover:bg-green-900/20"
                  : "text-blue-400 hover:text-blue-300 hover:bg-blue-900/20"
              )}
            >
              {isLoggedIn ? (
                loginMode === "cognito" && cognitoUser?.picture ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={cognitoUser.picture}
                    alt=""
                    className="h-3.5 w-3.5 rounded-full object-cover shrink-0"
                  />
                ) : (
                  <User className="h-3.5 w-3.5 shrink-0" />
                )
              ) : (
                <LogIn className="h-3.5 w-3.5 shrink-0" />
              )}
              <span className="truncate">
                {isLoggedIn
                  ? loginMode === "cognito"
                    ? cognitoUser?.name ?? cognitoUser?.email ?? "Signed in"
                    : "Dev logged in · persistent history"
                  : "Sign in with Google"}
              </span>
            </button>

            {isLoggedIn && (
              <button
                onClick={handleSignOut}
                className="shrink-0 flex items-center justify-center rounded-md p-1.5 text-zinc-500 hover:text-red-400 hover:bg-red-900/20 transition-colors"
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <DevLoginModal open={loginOpen && loginMode === "dev"} onClose={() => setLoginOpen(false)} />
        <CognitoLoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
      </aside>
    </>
  );
}
