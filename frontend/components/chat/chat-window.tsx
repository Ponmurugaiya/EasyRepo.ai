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
    (m) => m.role === "assistant" && "loading" in m && (m as { loading?: boolean }).loading === true
  );

  // AbortController ref — lets us cancel the in-flight request when user
  // clicks the stop button or when the component unmounts.
  const abortRef = useRef<AbortController | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);

  // Cancel any in-flight request when the component unmounts (e.g. switching repos)
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const handleSend = useCallback(
    async (content: string) => {
      if (repo.status !== "ready") return;

      // Cancel any previous in-flight request before starting a new one
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      addUserMessage(repo.repoId, content);
      const loadingId = addLoadingMessage(repo.repoId);

      // Snapshot conversation history BEFORE adding the new user message
      const MAX_HISTORY_TURNS = 20;
      const priorMessages = conversation?.messages ?? [];
      const historyTurns = priorMessages
        .filter(
          (m): m is (typeof m & { role: "user" | "assistant" }) =>
            (m.role === "user" || m.role === "assistant") &&
            !("loading" in m && (m as { loading?: boolean }).loading)
        )
        .slice(-MAX_HISTORY_TURNS)
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

      try {
        const response = await askRepository(
          repo.repoId,
          {
            query: content,
            top_k: 10,
            conversation_id: conversation?.id,
            conversation_history: historyTurns,
          },
          controller.signal
        );
        resolveLoadingMessage(repo.repoId, loadingId, response);
      } catch (err: unknown) {
        // User-initiated cancel — remove the loading bubble silently
        if (err instanceof DOMException && err.name === "AbortError") {
          setErrorMessage(repo.repoId, loadingId, "Request cancelled.");
          return;
        }
        const msg =
          err instanceof Error ? err.message : "Request failed. Please try again.";
        setErrorMessage(repo.repoId, loadingId, msg);
      } finally {
        // Safety net: clear the controller so the ref doesn't hold a stale abort
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
      }
    },
    [
      repo.repoId,
      repo.status,
      conversation,
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
                <ChatMessage key={message.id} message={message} repoId={repo.repoId} />
              ))}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}
      </div>

      <div className="border-t border-zinc-800">
        <ChatInput
          onSend={handleSend}
          onCancel={handleCancel}
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
