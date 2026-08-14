// ─────────────────────────────────────────────────────────────────────────────
// Chat store — manages conversations and repo sessions in memory
// repoSessions and activeRepoId are persisted to localStorage so the sidebar
// survives browser refresh without re-adding repos.
// Conversations are NOT persisted (they reset on refresh — intentional).
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  ChatMessage,
  Conversation,
  RepoSession,
} from "../types/chat";
import type { AskResponse, RepositoryResponse } from "../types/api";

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

interface ChatState {
  // Active conversation IDs keyed by repoId
  conversations: Record<string, Conversation>;
  // Active repo id being chatted with
  activeRepoId: string | null;
  // Repos that have been added this session
  repoSessions: Record<string, RepoSession>;
  // Whether the sidebar is open
  sidebarOpen: boolean;
  // Set to true when "New Chat" is clicked — causes the next repo pick from
  // the WelcomeScreen to always start a fresh conversation instead of
  // resuming the existing one.
  newChatMode: boolean;

  // Actions
  setSidebarOpen: (open: boolean) => void;
  addRepoSession: (repo: RepositoryResponse) => void;
  updateRepoSession: (repoId: string, updates: Partial<RepoSession>) => void;
  /** Set the active repo. Pass forceNew=true to always start a fresh conversation. */
  setActiveRepo: (repoId: string | null, forceNew?: boolean) => void;
  /** Called by the "New Chat" button — navigates to WelcomeScreen and flags
   *  the next repo pick to start a fresh conversation. */
  startNewChat: () => void;

  startConversation: (repoId: string) => string; // returns conversationId
  getConversation: (repoId: string) => Conversation | undefined;
  addUserMessage: (repoId: string, content: string) => string; // returns msgId
  addLoadingMessage: (repoId: string) => string;
  resolveLoadingMessage: (
    repoId: string,
    loadingId: string,
    response: AskResponse
  ) => void;
  setErrorMessage: (
    repoId: string,
    loadingId: string,
    error: string
  ) => void;
  clearConversation: (repoId: string) => void;
  /** Remove a pending/loading message without leaving any error bubble. */
  removePendingMessage: (repoId: string, msgId: string) => void;
  /** Replace the repo session list with the server's authoritative list.
   *  Called after dev login to prune repos the user has no access to. */
  syncRepoSessions: (repos: RepositoryResponse[]) => void;
  /** Restore conversation history from the server for a repo (authenticated users). */
  restoreHistory: (repoId: string) => Promise<void>;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      conversations: {},
      activeRepoId: null,
      repoSessions: {},
      sidebarOpen: true,
      newChatMode: false,

      setSidebarOpen: (open) => set({ sidebarOpen: open }),

      addRepoSession: (repo) =>
        set((state) => ({
          repoSessions: {
            ...state.repoSessions,
            [repo.repo_id]: {
              repoId: repo.repo_id,
              repoName: repo.name,
              repoUrl: repo.url_or_path,
              status: repo.status,
              entityCount: repo.entity_count,
              relationshipCount: repo.relationship_count,
              indexedAt: repo.indexed_at,
            },
          },
        })),

      updateRepoSession: (repoId, updates) =>
        set((state) => ({
          repoSessions: {
            ...state.repoSessions,
            [repoId]: { ...state.repoSessions[repoId], ...updates },
          },
        })),

      setActiveRepo: (repoId, forceNew = false) => {
        set({ activeRepoId: repoId, newChatMode: false });
        if (repoId && (forceNew || !get().conversations[repoId])) {
          get().startConversation(repoId);
        }
      },

      startNewChat: () => {
        set({ activeRepoId: null, newChatMode: true });
      },

      startConversation: (repoId) => {
        const repo = get().repoSessions[repoId];
        const conv: Conversation = {
          id: uid(),
          repoId,
          repoName: repo?.repoName ?? repoId,
          repoUrl: repo?.repoUrl ?? "",
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        set((state) => ({
          conversations: { ...state.conversations, [repoId]: conv },
        }));
        return conv.id;
      },

      getConversation: (repoId) => get().conversations[repoId],

      addUserMessage: (repoId, content) => {
        const msgId = uid();
        const msg: ChatMessage = {
          id: msgId,
          role: "user",
          content,
          timestamp: Date.now(),
        };
        set((state) => {
          const conv = state.conversations[repoId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [repoId]: {
                ...conv,
                messages: [...conv.messages, msg],
                updatedAt: Date.now(),
              },
            },
          };
        });
        return msgId;
      },

      addLoadingMessage: (repoId) => {
        const msgId = uid();
        const msg: ChatMessage = {
          id: msgId,
          role: "assistant",
          content: "",
          timestamp: Date.now(),
          loading: true,
        };
        set((state) => {
          const conv = state.conversations[repoId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [repoId]: {
                ...conv,
                messages: [...conv.messages, msg],
                updatedAt: Date.now(),
              },
            },
          };
        });
        return msgId;
      },

      resolveLoadingMessage: (repoId, loadingId, response) => {
        set((state) => {
          const conv = state.conversations[repoId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [repoId]: {
                ...conv,
                messages: conv.messages.map((m) =>
                  m.id === loadingId
                    ? {
                        id: loadingId,
                        role: "assistant" as const,
                        content: response.answer,
                        timestamp: Date.now(),
                        provider: response.provider,
                        citations: response.citations,
                        context_entities: response.context_entities,
                        loading: false as const,
                      }
                    : m
                ),
                updatedAt: Date.now(),
              },
            },
          };
        });
      },

      setErrorMessage: (repoId, loadingId, error) => {
        set((state) => {
          const conv = state.conversations[repoId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [repoId]: {
                ...conv,
                messages: conv.messages.map((m) =>
                  m.id === loadingId
                    ? {
                        id: loadingId,
                        role: "error" as const,
                        content: error,
                        timestamp: Date.now(),
                      }
                    : m
                ),
                updatedAt: Date.now(),
              },
            },
          };
        });
      },

      clearConversation: (repoId) => {
        const repo = get().repoSessions[repoId];
        const conv: Conversation = {
          id: uid(),
          repoId,
          repoName: repo?.repoName ?? repoId,
          repoUrl: repo?.repoUrl ?? "",
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        set((state) => ({
          conversations: { ...state.conversations, [repoId]: conv },
        }));
      },

      removePendingMessage: (repoId, msgId) => {
        set((state) => {
          const conv = state.conversations[repoId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [repoId]: {
                ...conv,
                messages: conv.messages.filter((m) => m.id !== msgId),
                updatedAt: Date.now(),
              },
            },
          };
        });
      },

      syncRepoSessions: (repos) => {
        // Build new session map from server response
        const newSessions: Record<string, RepoSession> = {};
        for (const repo of repos) {
          newSessions[repo.repo_id] = {
            repoId: repo.repo_id,
            repoName: repo.name,
            repoUrl: repo.url_or_path,
            status: repo.status,
            entityCount: repo.entity_count,
            relationshipCount: repo.relationship_count,
            indexedAt: repo.indexed_at,
          };
        }
        // Prune active repo and conversations for repos no longer accessible
        const accessibleIds = new Set(Object.keys(newSessions));
        set((state) => {
          const prunedConversations = Object.fromEntries(
            Object.entries(state.conversations).filter(([id]) => accessibleIds.has(id))
          );
          const nextActiveId =
            state.activeRepoId && accessibleIds.has(state.activeRepoId)
              ? state.activeRepoId
              : null;
          return {
            repoSessions: newSessions,
            conversations: prunedConversations,
            activeRepoId: nextActiveId,
          };
        });
      },

      restoreHistory: async (repoId) => {
        try {
          const { listConversations } = await import("../lib/api");
          const serverConvs = await listConversations(repoId, 1); // most recent only
          if (!serverConvs.length) return;

          const serverConv = serverConvs[0];
          if (!serverConv.turns.length) return;

          // Only restore if we don't already have local messages for this repo
          const existing = get().conversations[repoId];
          if (existing?.messages.length) return;

          // Rebuild messages from server turns
          const messages: ChatMessage[] = serverConv.turns.map((t) => {
            if (t.role === "user") {
              return {
                id: uid(),
                role: "user" as const,
                content: t.content,
                timestamp: new Date(t.created_at).getTime(),
              };
            }
            return {
              id: uid(),
              role: "assistant" as const,
              content: t.content,
              timestamp: new Date(t.created_at).getTime(),
              provider: "restored",
              citations: {
                total_citations: 0,
                definition_citations: [],
                call_site_citations: [],
                unsupported_citations: [],
                hallucination_rate: 0,
              },
              context_entities: [],
              loading: false as const,
            };
          });

          const repo = get().repoSessions[repoId];
          const restoredConv: Conversation = {
            id: serverConv.conversation_id,
            repoId,
            repoName: repo?.repoName ?? repoId,
            repoUrl: repo?.repoUrl ?? "",
            messages,
            createdAt: new Date(serverConv.created_at).getTime(),
            updatedAt: new Date(serverConv.updated_at).getTime(),
          };

          set((state) => ({
            conversations: {
              ...state.conversations,
              [repoId]: restoredConv,
            },
          }));
        } catch {
          // Non-fatal — silent failure, user just won't see history
        }
      },
    }),
    {
      name: "easyrepo-chat-store",          // localStorage key
      storage: createJSONStorage(() => localStorage),
      // Persist repo sessions, active repo, sidebar state, AND conversations.
      // For anonymous users the conversations are cleared on explicit "New chat"
      // but survive accidental refresh (intentional UX improvement).
      // For dev-login users the backend DB is the authoritative store; the
      // localStorage copy is just a display buffer that survives refresh.
      partialize: (state) => ({
        repoSessions: state.repoSessions,
        activeRepoId: state.activeRepoId,
        sidebarOpen: state.sidebarOpen,
        conversations: state.conversations,
      }),
    }
  )
);
