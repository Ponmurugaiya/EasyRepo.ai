"use client";

import { cn } from "../../lib/utils";
import { MessageSquare } from "lucide-react";
import type { Conversation } from "../../types/chat";
import { conversationTitle } from "../../store/chat-store";

interface ConversationItemProps {
  conversation: Conversation;
  active: boolean;
  onClick: () => void;
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins  = Math.floor(diff / 60_000);
  const hours = Math.floor(diff / 3_600_000);
  const days  = Math.floor(diff / 86_400_000);
  if (mins  < 1)  return "just now";
  if (mins  < 60) return `${mins}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days  < 7)  return `${days}d ago`;
  return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function ConversationItem({
  conversation,
  active,
  onClick,
}: ConversationItemProps) {
  const title = conversationTitle(conversation);
  const isEmpty = conversation.messages.length === 0;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-lg transition-colors cursor-pointer select-none group",
        "hover:bg-white/5",
        active && "bg-white/10"
      )}
    >
      <div className="flex items-start gap-2.5">
        <MessageSquare
          className={cn(
            "mt-0.5 h-3.5 w-3.5 shrink-0 transition-colors",
            active ? "text-blue-400" : "text-zinc-500"
          )}
        />
        <div className="min-w-0 flex-1">
          {/* Conversation title — first user message */}
          <p
            className={cn(
              "text-sm truncate leading-snug",
              active ? "text-white font-medium" : "text-zinc-300",
              isEmpty && "text-zinc-500 italic"
            )}
          >
            {title}
          </p>
          {/* Repo name + timestamp */}
          <div className="flex items-center justify-between gap-2 mt-0.5">
            <span className="text-xs text-zinc-500 truncate">
              {conversation.repoName}
            </span>
            <span className="text-[10px] text-zinc-600 shrink-0">
              {relativeTime(conversation.updatedAt)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
