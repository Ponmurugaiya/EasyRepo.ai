"use client";

import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/store/chat-store";
import { askRepository } from "@/lib/api";
import { ChatMessage } from "./chat-message";
import { ChatInput } from "./chat-input";
import { EmptyState } from "./empty-state";
import { ChatHeader } from "./chat-header";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { RepoSession } from "@/types/chat";

interface ChatWindowProps {
  repo: RepoSession;
}

export function ChatWindow({ repo }: ChatWindowProps) {
  const {
    conversations,
    addUserMessage,
    addLoadingMessage,
    resolveLoadingMessage,
    setErrorMessage,
    clearConversation,
  } = useChatStore();

  const conversation = conversations[repo.repoId];
  const messages = conversation?.messages ?? [];
  const isLoading = messages.some(
    (m) => m.role === "assistant" && "loading" in m && m.loading
  );

  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const handleSend = useCallback(
    async (content: string) => {
      if (repo.status !== "ready") return;

      addUserMessage(repo.repoId, content);
      const loadingId = addLoadingMessage(repo.repoId);

      try {
        const response = await askRepository(repo.repoId, {
          query: content,
          top_k: 10,
        });
        resolveLoadingMessage(repo.repoId, loadingId, response);
      } catch (err: unknown) {
        const msg =
          err instanceof Error ? err.message : "Request failed. Please try again.";
        setErrorMessage(repo.repoId, loadingId, msg);
      }
    },
    [
      repo.repoId,
      repo.status,
      addUserMessage,
      addLoadingMessage,
      resolveLoadingMessage,
      setErrorMessage,
    ]
  );

  const disabled = repo.status !== "ready";

  return (
    <div className="flex flex-col h-full">
      <ChatHeader repo={repo} onClear={() => clearConversation(repo.repoId)} />

      <div className="flex-1 overflow-hidden">
        {messages.length === 0 ? (
          <EmptyState repo={repo} onSuggest={handleSend} />
        ) : (
          <ScrollArea className="h-full">
            <div className="pb-4">
              {messages.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}
      </div>

      <div className="border-t border-zinc-800">
        <ChatInput
          onSend={handleSend}
          disabled={disabled}
          loading={isLoading}
          placeholder={
            disabled
              ? `Repository is ${repo.status}…`
              : "Ask anything about this codebase…"
          }
        />
      </div>
    </div>
  );
}
