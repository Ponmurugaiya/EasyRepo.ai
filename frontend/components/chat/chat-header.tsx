"use client";

import { StatusBadge } from "../../components/ui/status-badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { PanelLeft, ExternalLink, FileSearch, Zap, LogIn, User } from "lucide-react";
import { useChatStore } from "../../store/chat-store";
import { useAuthStore } from "../../store/auth-store";
import { truncate, cn } from "../../lib/utils";
import type { RepoSession } from "../../types/chat";

interface ChatHeaderProps {
  repo: RepoSession;
  onClear: () => void;
  onLoginClick: () => void;
}

const iconBtn = cn(
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg",
  "text-zinc-400 hover:text-white hover:bg-white/5 transition-colors",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500"
);

export function ChatHeader({ repo, onClear, onLoginClick }: ChatHeaderProps) {
  const { sidebarOpen, setSidebarOpen } = useChatStore();
  const { isLoggedIn, cognitoUser, loginMode } = useAuthStore();
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

        {/* Login / Profile button */}
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                className={cn(
                  iconBtn,
                  isLoggedIn
                    ? "text-green-400 hover:text-green-300"
                    : "text-blue-400 hover:text-blue-300"
                )}
                onClick={onLoginClick}
                aria-label={isLoggedIn ? "Account" : "Sign in"}
              >
                {isLoggedIn && loginMode === "cognito" && cognitoUser?.picture ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={cognitoUser.picture}
                    alt={cognitoUser.name ?? cognitoUser.email}
                    className="h-5 w-5 rounded-full object-cover"
                  />
                ) : isLoggedIn ? (
                  <User className="h-4 w-4" />
                ) : (
                  <LogIn className="h-4 w-4" />
                )}
              </button>
            }
          />
          <TooltipContent>
            {isLoggedIn
              ? loginMode === "cognito"
                ? cognitoUser?.name ?? "Signed in"
                : "Dev logged in"
              : "Sign in"}
          </TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
