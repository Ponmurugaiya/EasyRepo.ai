"use client";

import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/spinner";
import { CitationPanel } from "./citation-panel";
import { MarkdownContent } from "./markdown-content";
import { Bot, User, AlertTriangle } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/types/chat";

interface ChatMessageProps {
  message: ChatMessageType;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isError = message.role === "error";

  if (message.role === "assistant" && "loading" in message && message.loading) {
    return (
      <div className="flex gap-3 px-4 py-5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-600">
          <Bot className="h-4 w-4 text-white" />
        </div>
        <div className="flex items-center gap-2 pt-1">
          <Spinner size="sm" className="text-blue-400" />
          <span className="text-sm text-zinc-500">Thinking…</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group flex gap-3 px-4 py-5",
        isUser ? "flex-row-reverse" : "flex-row"
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-zinc-700"
            : isError
            ? "bg-red-800"
            : "bg-blue-600"
        )}
      >
        {isUser ? (
          <User className="h-4 w-4 text-zinc-200" />
        ) : isError ? (
          <AlertTriangle className="h-4 w-4 text-red-300" />
        ) : (
          <Bot className="h-4 w-4 text-white" />
        )}
      </div>

      {/* Content */}
      <div
        className={cn(
          "min-w-0 max-w-[80%] space-y-3",
          isUser ? "items-end" : "items-start"
        )}
      >
        {isUser ? (
          <div className="rounded-2xl rounded-tr-sm bg-blue-600 px-4 py-2.5 text-sm text-white">
            {message.content}
          </div>
        ) : isError ? (
          <div className="rounded-2xl rounded-tl-sm border border-red-800 bg-red-950/50 px-4 py-2.5 text-sm text-red-300">
            <p className="font-medium mb-1">Request failed</p>
            <p className="text-red-400">{message.content}</p>
          </div>
        ) : (
          <>
            <div className="rounded-2xl rounded-tl-sm bg-zinc-800/60 px-4 py-3">
              <MarkdownContent content={message.content} />
              {/* Provider chip */}
              {"provider" in message && message.provider && (
                <div className="mt-3 pt-2 border-t border-zinc-700/50">
                  <span className="text-xs text-zinc-600">
                    via {message.provider}
                  </span>
                </div>
              )}
            </div>
            {/* Citations */}
            {"citations" in message && message.citations && (
              <CitationPanel citations={message.citations} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
