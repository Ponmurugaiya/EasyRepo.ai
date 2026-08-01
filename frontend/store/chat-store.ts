// ─────────────────────────────────────────────────────────────────────────────
// Chat store — manages conversations and repo sessions in memory
// Phase 1: no persistence (temporary chat). Phase 2 will add IndexedDB.
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import type {
  ChatMessage,
  Conversation,
  RepoSession,
} from "@/types/chat";
import type { AskResponse, RepositoryResponse } from "@/types/api";

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

  // Actions
  setSidebarOpen: (open: boolean) => void;
  addRepoSession: (repo: RepositoryResponse) => void;
  updateRepoSession: (repoId: string, updates: Partial<RepoSession>) => void;
  setActiveRepo: (repoId: string | null) => void;

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
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: {},
  activeRepoId: null,
  repoSessions: {},
  sidebarOpen: true,

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

  setActiveRepo: (repoId) => {
    set({ activeRepoId: repoId });
    if (repoId && !get().conversations[repoId]) {
      get().startConversation(repoId);
    }
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
}));
