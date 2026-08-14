"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { useChatStore } from "../../store/chat-store";
import { useAuthStore } from "../../store/auth-store";
import { submitAskJob, getAskJob, ApiError } from "../../lib/api";
import { friendlyMessage } from "../../lib/errors";
import { sleep } from "../../lib/utils";
import { ChatMessage } from "./chat-message";
import { ChatInput } from "./chat-input";
import { EmptyState } from "./empty-state";
import { ChatHeader } from "./chat-header";
import { GuestBanner } from "./guest-banner";
import { CognitoLoginModal } from "../../components/auth/cognito-login-modal";
import { ScrollArea } from "../../components/ui/scroll-area";
import { LogIn } from "lucide-react";
import { Button } from "../../components/ui/button";
import type { RepoSession } from "../../types/chat";
import type { AskJobProgress } from "../../lib/api";

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
    removePendingMessage,
    clearConversation,
  } = useChatStore();

  const { isLoggedIn } = useAuthStore();

  const conversation = conversations[repo.repoId];
  const messages = conversation?.messages ?? [];
  const isLoading = messages.some(
    (m) => m.role === "assistant" && "loading" in m && (m as { loading?: boolean }).loading === true
  );

  // Count how many user messages have been sent in this session
  const userMessageCount = messages.filter((m) => m.role === "user").length;
  // After the first Q+A pair completes (first user message got a response), gate follow-ups
  const firstAnswerComplete =
    userMessageCount >= 1 &&
    messages.some((m) => m.role === "assistant" && !("loading" in m && (m as { loading?: boolean }).loading));
  const guestFollowUpBlocked = !isLoggedIn && firstAnswerComplete;

  // Login modal state — can be opened from the header button or follow-up gate
  const [loginOpen, setLoginOpen] = useState(false);
  const [followUpGate, setFollowUpGate] = useState(false);
  // Live pipeline progress from the backend while a job is running
  const [jobProgress, setJobProgress] = useState<AskJobProgress | null>(null);

  function openLoginForFollowUp() {
    setFollowUpGate(true);
    setLoginOpen(true);
  }

  function openLoginFromHeader() {
    setFollowUpGate(false);
    setLoginOpen(true);
  }

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

      // Guest follow-up gate: if the user is not logged in and already asked
      // one question, intercept and show the login modal instead of sending.
      if (!isLoggedIn && userMessageCount >= 1) {
        openLoginForFollowUp();
        return;
      }

      // Cancel any previous in-flight request before starting a new one
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      addUserMessage(repo.repoId, content);
      const loadingId = addLoadingMessage(repo.repoId);
      setJobProgress(null);

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
        // Submit job — returns immediately with job_id
        const submitted = await submitAskJob(
          repo.repoId,
          {
            query: content,
            top_k: 10,
            conversation_id: conversation?.id,
            conversation_history: historyTurns,
          },
          controller.signal
        );

        // Poll until done/failed, updating progress on each tick
        const POLL_INTERVAL_MS = 2000;
        const TIMEOUT_MS = 120_000;
        const deadline = Date.now() + TIMEOUT_MS;

        while (Date.now() < deadline) {
          if (controller.signal.aborted) throw new ApiError("Request cancelled.", 0);
          await sleep(POLL_INTERVAL_MS);
          if (controller.signal.aborted) throw new ApiError("Request cancelled.", 0);

          const job = await getAskJob(repo.repoId, submitted.job_id, controller.signal);

          // Update live progress whenever it's present
          if (job.progress) {
            setJobProgress(job.progress);
          }

          if (job.status === "done" && job.result) {
            setJobProgress(null);
            resolveLoadingMessage(repo.repoId, loadingId, job.result);
            return;
          }

          if (job.status === "failed") {
            setJobProgress(null);
            throw new ApiError(
              job.error ?? "The request failed on the server. Please try again.",
              500
            );
          }
        }

        throw new ApiError(
          "Request timed out. The server is taking too long — please try again.",
          408
        );
      } catch (err: unknown) {
        // User-initiated cancel — remove the loading bubble silently
        if (
          (err instanceof DOMException && err.name === "AbortError") ||
          (err instanceof ApiError && err.status === 0)
        ) {
          setJobProgress(null);
          removePendingMessage(repo.repoId, loadingId);
          return;
        }
        setJobProgress(null);
        const msg =
          err instanceof ApiError
            ? friendlyMessage(err)
            : "Something went wrong. Please try again.";
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
      isLoggedIn,
      userMessageCount,
      addUserMessage,
      addLoadingMessage,
      resolveLoadingMessage,
      removePendingMessage,
      setErrorMessage,
    ]
  );

  const disabled = repo.status !== "ready";

  return (
    <div className="flex flex-col h-full">
      <ChatHeader
        repo={repo}
        onClear={() => clearConversation(repo.repoId)}
        onLoginClick={openLoginFromHeader}
      />

      {/* Guest session banners */}
      {!isLoggedIn && (
        <GuestBanner
          onSignIn={openLoginFromHeader}
          questionUsed={guestFollowUpBlocked}
        />
      )}

      <div className="flex-1 overflow-hidden">
        {messages.length === 0 ? (
          <EmptyState repo={repo} onSuggest={handleSend} />
        ) : (
          <ScrollArea className="h-full">
            <div className="pb-4">
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  repoId={repo.repoId}
                  jobProgress={
                    message.role === "assistant" &&
                    "loading" in message &&
                    (message as { loading?: boolean }).loading
                      ? jobProgress
                      : null
                  }
                />
              ))}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}
      </div>

      <div className="border-t border-zinc-800">
        {/* Follow-up gate for guest users: replace input with a sign-in prompt */}
        {guestFollowUpBlocked ? (
          <div className="px-4 py-5 flex flex-col items-center gap-3 text-center">
            <p className="text-sm text-zinc-400">
              Sign in to ask follow-up questions and keep your chat history.
            </p>
            <Button
              onClick={openLoginForFollowUp}
              className="gap-2 bg-blue-600 hover:bg-blue-500"
            >
              <LogIn className="h-4 w-4" />
              Sign in to continue
            </Button>
          </div>
        ) : (
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
        )}
      </div>

      {/* Login modal — opened from header button or follow-up gate */}
      <CognitoLoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        followUpGate={followUpGate}
      />
    </div>
  );
}
