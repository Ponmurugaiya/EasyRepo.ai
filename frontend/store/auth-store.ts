// ─────────────────────────────────────────────────────────────────────────────
// Auth store — supports two login modes:
//   1. Dev API key (er_xxx token) — stored in localStorage, sent as X-API-Key
//   2. Cognito/Google JWT — managed by aws-amplify, stored in localStorage
//      by Amplify's own CognitoUserPool mechanism
//
// The `isLoggedIn` flag is true for either mode. Components should use
// `isLoggedIn` to gate gated features and `loginMode` to branch UI.
// ─────────────────────────────────────────────────────────────────────────────

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type LoginMode = "dev" | "cognito" | null;

interface CognitoUser {
  username: string;
  email: string;
  name?: string;
  picture?: string;
}

interface AuthState {
  // ── Dev token (legacy) ──────────────────────────────────────────────────────
  /** The raw API token string (er_xxx...), or null when not using dev login. */
  token: string | null;

  // ── Cognito ─────────────────────────────────────────────────────────────────
  /** Basic user profile returned from Cognito after OAuth. */
  cognitoUser: CognitoUser | null;

  // ── Shared ──────────────────────────────────────────────────────────────────
  /** Which login mode is active, or null when logged out. */
  loginMode: LoginMode;
  /** Derived: true when any login is active. */
  isLoggedIn: boolean;

  // ── Actions ─────────────────────────────────────────────────────────────────
  /** Dev token login. */
  setToken: (token: string) => void;
  /** Called after Cognito redirect — stores user profile. */
  setCognitoUser: (user: CognitoUser) => void;
  /** Clear all auth state (dev or Cognito). */
  clearToken: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      cognitoUser: null,
      loginMode: null,
      isLoggedIn: false,

      setToken: (token) =>
        set({
          token: token.trim() || null,
          cognitoUser: null,
          loginMode: token.trim() ? "dev" : null,
          isLoggedIn: Boolean(token.trim()),
        }),

      setCognitoUser: (user) =>
        set({
          cognitoUser: user,
          token: null,
          loginMode: "cognito",
          isLoggedIn: true,
        }),

      clearToken: () =>
        set({
          token: null,
          cognitoUser: null,
          loginMode: null,
          isLoggedIn: false,
        }),
    }),
    {
      name: "easyrepo-dev-token",
      storage: createJSONStorage(() => localStorage),
    }
  )
);
