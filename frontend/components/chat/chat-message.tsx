"use client";

import { useState, useMemo } from "react";
import { cn } from "../../lib/utils";
import { Spinner } from "../../components/ui/spinner";
import { CitationPanel } from "./citation-panel";
import { CitationCodeViewer } from "./citation-code-viewer";
import { MarkdownContent } from "./markdown-content";
import { Bot, User, AlertTriangle, GitBranch } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "../../types/chat";
import { buildCitationMap } from "../../lib/citations";
import type { ResolvedCitation } from "../../lib/citations";
import { useGraphStore } from "../../store/graph-store";

interface ChatMessageProps {
  message: ChatMessageType;
  /** The repo id, needed to fetch entity source code */
  repoId: string;
}

export function ChatMessage({ message, repoId }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isError = message.role === "error";
  const { highlightEntity } = useGraphStore();

  // Active citation state for the code viewer modal
  const [activeCitation, setActiveCitation] = useState<{
    index: number;
    citation: ResolvedCitation;
  } | null>(null);

  // Build citation map once per message (memoised on content + citations).
  // NOTE: AssistantMessage has loading?: false (field present but false when resolved).
  // We must check the VALUE, not just presence, to avoid skipping resolved messages.
  const isResolved =
    message.role === "assistant" &&
    !("loading" in message && message.loading === true) &&
    "citations" in message;

  const { processedContent, citations: citationMap } = useMemo(() => {
    if (!isResolved || !("citations" in message) || !message.citations) {
      return { processedContent: message.content, citations: new Map() };
    }
    return buildCitationMap(message.content, message.citations);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isResolved, message.content, "citations" in message ? message.citations : null]);

  const handleCitationClick = (index: number, citation: ResolvedCitation) => {
    setActiveCitation({ index, citation });
  };

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
    <>
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
                <MarkdownContent
                  content={processedContent}
                  citationMap={citationMap}
                  onCitationClick={handleCitationClick}
                />
                {/* Provider chip */}
                {"provider" in message && message.provider && (
                  <div className="mt-3 pt-2 border-t border-zinc-700/50">
                    <span className="text-xs text-zinc-600">
                      via {message.provider}
                    </span>
                  </div>
                )}
              </div>
              {/* Citations panel */}
              {"citations" in message && message.citations && (
                <CitationPanel
                  citations={message.citations}
                  citationMap={citationMap}
                  onCitationClick={handleCitationClick}
                  onShowInGraph={(entityId) => highlightEntity(entityId, repoId)}
                />
              )}
            </>
          )}
        </div>
      </div>

      {/* Code viewer modal — rendered outside the message bubble */}
      {activeCitation && (
        <CitationCodeViewer
          repoId={repoId}
          citation={activeCitation.citation}
          citationIndex={activeCitation.index}
          onClose={() => setActiveCitation(null)}
        />
      )}
    </>
  );
}
