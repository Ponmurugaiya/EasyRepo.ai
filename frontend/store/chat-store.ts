// ─────────────────────────────────────────────────────────────────────────────
// Chat store — manages conversations and repo sessions
//
// Data model change: conversations are now keyed by conversationId (not
// repoId) so multiple conversations per repo are supported. The sidebar
// shows a history list with repo name + first user message as the title.
//
// Persisted to localStorage: repoSessions, conversationList, activeConversationId,
// sidebarOpen. Conversations survive refresh and "New Chat" no longer
// destroys old history.
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

/** Derive the display title for a conversation from its first user message. */
export function conversationTitle(conv: Conversation): string {
  const first = conv.messages.find((m) => m.role === "user");
  if (!first) return "New conversation";
  const text = first.content.trim();
  return text.length > 60 ? text.slice(0, 57) + "…" : text;
}

interface ChatState {
  // All conversations keyed by conversationId
  conversationList: Record<string, Conversation>;
  // Currently open conversation ID
  activeConversationId: string | null;
  // Repos that have been added
  repoSessions: Record<string, RepoSession>;
  // Whether the sidebar is open
  sidebarOpen: boolean;
  // Legacy field kept for WelcomeScreen repo picker
  activeRepoId: string | null;
  // Deprecated — kept for backwards compat with WelcomeScreen
  newChatMode: boolean;

  // Actions
  setSidebarOpen: (open: boolean) => void;
  addRepoSession: (repo: RepositoryResponse) => void;
  updateRepoSession: (repoId: string, updates: Partial<RepoSession>) => void;

  /** Open an existing conversation or start a new one for the given repo. */
  setActiveRepo: (repoId: string | null, forceNew?: boolean) => void;
  /** Open a specific conversation by ID. */
  openConversation: (conversationId: string) => void;
  /** Start a brand-new empty conversation for the given repo. */
  startNewChatForRepo: (repoId: string) => string; // returns conversationId
  /** "New Chat" button — deselects active conversation, shows WelcomeScreen. */
  startNewChat: () => void;

  // Internal helpers called by ChatWindow
  startConversation: (repoId: string) => string;
  getConversation: (repoId: string) => Conversation | undefined;
  getActiveConversation: () => Conversation | undefined;
  addUserMessage: (repoId: string, content: string) => string;
  addLoadingMessage: (repoId: string) => string;
  resolveLoadingMessage: (
    repoId: string,
    loadingId: string,
    response: AskResponse
  ) => void;
  setErrorMessage: (repoId: string, loadingId: string, error: string) => void;
  clearConversation: (repoId: string) => void;
  removePendingMessage: (repoId: string, msgId: string) => void;
  syncRepoSessions: (repos: RepositoryResponse[]) => void;
  restoreHistory: (repoId: string) => Promise<void>;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      conversationList: {},
      activeConversationId: null,
      repoSessions: {},
      sidebarOpen: true,
      activeRepoId: null,
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

      // ── Conversation management ─────────────────────────────────────────

      setActiveRepo: (repoId, forceNew = false) => {
        if (!repoId) {
          set({ activeRepoId: null, activeConversationId: null, newChatMode: false });
          return;
        }
        set({ activeRepoId: repoId, newChatMode: false });

        if (forceNew) {
          // Start a fresh conversation — don't touch existing ones
          get().startNewChatForRepo(repoId);
          return;
        }

        // Find the most recent conversation for this repo
        const existing = Object.values(get().conversationList)
          .filter((c) => c.repoId === repoId)
          .sort((a, b) => b.updatedAt - a.updatedAt)[0];

        if (existing) {
          set({ activeConversationId: existing.id });
        } else {
          get().startNewChatForRepo(repoId);
        }
      },

      openConversation: (conversationId) => {
        const conv = get().conversationList[conversationId];
        if (!conv) return;
        set({
          activeConversationId: conversationId,
          activeRepoId: conv.repoId,
          newChatMode: false,
        });
      },

      startNewChatForRepo: (repoId) => {
        const repo = get().repoSessions[repoId];
        const convId = uid();
        const conv: Conversation = {
          id: convId,
          repoId,
          repoName: repo?.repoName ?? repoId,
          repoUrl: repo?.repoUrl ?? "",
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        };
        set((state) => ({
          conversationList: { ...state.conversationList, [convId]: conv },
          activeConversationId: convId,
          activeRepoId: repoId,
        }));
        return convId;
      },

      startNewChat: () => {
        set({ activeRepoId: null, activeConversationId: null, newChatMode: true });
      },

      // ── Legacy helpers (ChatWindow uses repoId-based API) ──────────────
      // These operate on the ACTIVE conversation for the given repoId.

      startConversation: (repoId) => {
        return get().startNewChatForRepo(repoId);
      },

      getConversation: (repoId) => {
        const { activeConversationId, conversationList } = get();
        if (activeConversationId) {
          const active = conversationList[activeConversationId];
          if (active && active.repoId === repoId) return active;
        }
        // Fallback: most recent conversation for this repo
        return Object.values(conversationList)
          .filter((c) => c.repoId === repoId)
          .sort((a, b) => b.updatedAt - a.updatedAt)[0];
      },

      getActiveConversation: () => {
        const { activeConversationId, conversationList } = get();
        return activeConversationId ? conversationList[activeConversationId] : undefined;
      },

      addUserMessage: (repoId, content) => {
        const msgId = uid();
        const msg: ChatMessage = {
          id: msgId,
          role: "user",
          content,
          timestamp: Date.now(),
        };
        const conv = get().getConversation(repoId);
        if (!conv) return msgId;
        set((state) => ({
          conversationList: {
            ...state.conversationList,
            [conv.id]: {
              ...conv,
              messages: [...conv.messages, msg],
              updatedAt: Date.now(),
            },
          },
        }));
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
        const conv = get().getConversation(repoId);
        if (!conv) return msgId;
        set((state) => ({
          conversationList: {
            ...state.conversationList,
            [conv.id]: {
              ...conv,
              messages: [...conv.messages, msg],
              updatedAt: Date.now(),
            },
          },
        }));
        return msgId;
      },

      resolveLoadingMessage: (repoId, loadingId, response) => {
        const conv = get().getConversation(repoId);
        if (!conv) return;
        set((state) => ({
          conversationList: {
            ...state.conversationList,
            [conv.id]: {
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
                      is_overview: response.is_overview ?? false,
                    }
                  : m
              ),
              updatedAt: Date.now(),
            },
          },
        }));
      },

      setErrorMessage: (repoId, loadingId, error) => {
        const conv = get().getConversation(repoId);
        if (!conv) return;
        set((state) => ({
          conversationList: {
            ...state.conversationList,
            [conv.id]: {
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
        }));
      },

      clearConversation: (repoId) => {
        // "Clear" starts a fresh conversation for the repo (doesn't delete history)
        get().startNewChatForRepo(repoId);
      },

      removePendingMessage: (repoId, msgId) => {
        const conv = get().getConversation(repoId);
        if (!conv) return;
        set((state) => ({
          conversationList: {
            ...state.conversationList,
            [conv.id]: {
              ...conv,
              messages: conv.messages.filter((m) => m.id !== msgId),
              updatedAt: Date.now(),
            },
          },
        }));
      },

      syncRepoSessions: (repos) => {
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
        const accessibleIds = new Set(Object.keys(newSessions));
        set((state) => {
          const prunedConversations = Object.fromEntries(
            Object.entries(state.conversationList).filter(([, conv]) =>
              accessibleIds.has(conv.repoId)
            )
          );
          const nextActiveId =
            state.activeConversationId &&
            prunedConversations[state.activeConversationId]
              ? state.activeConversationId
              : null;
          return {
            repoSessions: newSessions,
            conversationList: prunedConversations,
            activeConversationId: nextActiveId,
            activeRepoId: nextActiveId
              ? prunedConversations[nextActiveId]?.repoId ?? null
              : null,
          };
        });
      },

      restoreHistory: async (repoId) => {
        try {
          const { listConversations } = await import("../lib/api");
          const serverConvs = await listConversations(repoId, 5);
          if (!serverConvs.length) return;

          const repo = get().repoSessions[repoId];

          // Import server conversations that aren't already in local state
          const existing = new Set(
            Object.values(get().conversationList)
              .filter((c) => c.repoId === repoId)
              .map((c) => c.id)
          );

          const toAdd: Record<string, Conversation> = {};
          for (const serverConv of serverConvs) {
            if (existing.has(serverConv.conversation_id)) continue;
            if (!serverConv.turns.length) continue;

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

            toAdd[serverConv.conversation_id] = {
              id: serverConv.conversation_id,
              repoId,
              repoName: repo?.repoName ?? repoId,
              repoUrl: repo?.repoUrl ?? "",
              messages,
              createdAt: new Date(serverConv.created_at).getTime(),
              updatedAt: new Date(serverConv.updated_at).getTime(),
            };
          }

          if (Object.keys(toAdd).length) {
            set((state) => ({
              conversationList: { ...state.conversationList, ...toAdd },
            }));
          }
        } catch {
          // Non-fatal
        }
      },
    }),
    {
      name: "easyrepo-chat-store-v2",   // new key — avoids stale shape from v1
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        repoSessions: state.repoSessions,
        activeRepoId: state.activeRepoId,
        activeConversationId: state.activeConversationId,
        conversationList: state.conversationList,
        sidebarOpen: state.sidebarOpen,
      }),
    }
  )
);
