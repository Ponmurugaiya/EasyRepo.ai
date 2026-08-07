// ─────────────────────────────────────────────────────────────────────────────
// Auth store — developer login state
//
// Stores an API token in localStorage under "easyrepo-dev-token".
// When a token is present it is attached to every backend request as
// X-API-Key, giving the backend a stable user_id so conversation turns are
// persisted in the DB and survive browser refresh.
//
// This is intentionally a thin local-only store — no OAuth, no session server.
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AuthState {
  /** The raw API token string, or null when logged out. */
  token: string | null;
  /** Derived: true when a token is stored. */
  isLoggedIn: boolean;

  setToken: (token: string) => void;
  clearToken: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      isLoggedIn: false,

      setToken: (token) =>
        set({ token: token.trim() || null, isLoggedIn: Boolean(token.trim()) }),

      clearToken: () => set({ token: null, isLoggedIn: false }),
    }),
    {
      name: "easyrepo-dev-token",
      storage: createJSONStorage(() => localStorage),
    }
  )
);
