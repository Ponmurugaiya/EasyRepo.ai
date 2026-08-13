"use client";

import { useState, useEffect } from "react";
import { useAuthStore } from "../../store/auth-store";
import { useChatStore } from "../../store/chat-store";
import { listRepositories } from "../../lib/api";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "../../components/ui/dialog";
import { LogIn, Loader2 } from "lucide-react";

interface CognitoLoginModalProps {
  open: boolean;
  onClose: () => void;
  /** When true, shows an extra message explaining why login is required. */
  followUpGate?: boolean;
}

export function CognitoLoginModal({
  open,
  onClose,
  followUpGate = false,
}: CognitoLoginModalProps) {
  const { isLoggedIn, setCognitoUser, clearToken } = useAuthStore();
  const { syncRepoSessions } = useChatStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // After Cognito redirects back, pick up the session automatically
  useEffect(() => {
    if (!open) return;

    async function checkSession() {
      try {
        const { configureCognito, getCognitoUser, getCognitoToken } = await import(
          "../../lib/cognito"
        );
        const configured = configureCognito();
        if (!configured) return; // Cognito env vars not set yet

        const user = await getCognitoUser();
        if (user) {
          const token = await getCognitoToken();
          if (token) {
            // Decode email / name from JWT payload (no extra network call)
            const payload = JSON.parse(atob(token.split(".")[1]));
            setCognitoUser({
              username: user.username,
              email: payload.email ?? user.username,
              name: payload.name ?? payload["cognito:username"] ?? user.username,
              picture: payload.picture,
            });
            // Sync repos
            try {
              const repos = await listRepositories();
              syncRepoSessions(repos);
            } catch {
              // Non-fatal
            }
            onClose();
          }
        }
      } catch {
        // No active session — user still needs to log in
      }
    }

    checkSession();
  }, [open, setCognitoUser, syncRepoSessions, onClose]);

  async function handleGoogleSignIn() {
    setLoading(true);
    setError(null);
    try {
      const { configureCognito, signInWithGoogle } = await import("../../lib/cognito");
      const configured = configureCognito();
      if (!configured) {
        setError(
          "Cognito is not configured. Set NEXT_PUBLIC_COGNITO_* environment variables."
        );
        setLoading(false);
        return;
      }
      await signInWithGoogle();
      // Page will redirect — nothing to do here
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
      setLoading(false);
    }
  }

  async function handleSignOut() {
    setLoading(true);
    try {
      const { configureCognito, signOutCognito } = await import("../../lib/cognito");
      configureCognito();
      await signOutCognito();
    } catch {
      // Best-effort
    }
    clearToken();
    try {
      const repos = await listRepositories();
      syncRepoSessions(repos);
    } catch {
      // Non-fatal
    }
    setLoading(false);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-zinc-900 border-zinc-700 text-zinc-100 max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-white">
            <LogIn className="h-4 w-4 text-blue-400" />
            {isLoggedIn ? "Your account" : "Sign in to EasyRepo"}
          </DialogTitle>
          {!isLoggedIn && (
            <DialogDescription className="text-zinc-400">
              {followUpGate
                ? "You've used your free question. Sign in to keep the conversation going — your history is saved permanently."
                : "Sign in to save your chat history and access your repositories across sessions."}
            </DialogDescription>
          )}
        </DialogHeader>

        {isLoggedIn ? (
          <SignedInView onSignOut={handleSignOut} loading={loading} />
        ) : (
          <div className="space-y-4 pt-1">
            {followUpGate && (
              <div className="rounded-lg border border-amber-700/40 bg-amber-900/20 px-4 py-3 text-sm text-amber-300">
                You've reached the limit for temporary sessions. Sign in to continue.
              </div>
            )}

            <Button
              className="w-full gap-2 bg-white text-zinc-900 hover:bg-zinc-100 font-medium"
              onClick={handleGoogleSignIn}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <GoogleIcon />
              )}
              {loading ? "Redirecting…" : "Continue with Google"}
            </Button>

            {error && (
              <p className="text-xs text-red-400 text-center">{error}</p>
            )}

            <p className="text-xs text-zinc-600 text-center leading-relaxed">
              Signing in with Google is handled securely via AWS Cognito.
              Your credentials never touch our servers.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function SignedInView({
  onSignOut,
  loading,
}: {
  onSignOut: () => void;
  loading: boolean;
}) {
  const { cognitoUser } = useAuthStore();

  return (
    <div className="space-y-4 pt-1">
      <div className="rounded-lg border border-green-700/40 bg-green-900/20 px-4 py-3 flex items-center gap-3">
        {cognitoUser?.picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={cognitoUser.picture}
            alt={cognitoUser.name ?? cognitoUser.email}
            className="h-8 w-8 rounded-full object-cover"
          />
        ) : (
          <div className="h-8 w-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-sm font-bold shrink-0">
            {(cognitoUser?.name ?? cognitoUser?.email ?? "?")[0].toUpperCase()}
          </div>
        )}
        <div className="min-w-0">
          <p className="text-sm font-medium text-green-400 truncate">
            {cognitoUser?.name ?? cognitoUser?.email ?? "Signed in"}
          </p>
          {cognitoUser?.email && cognitoUser?.name && (
            <p className="text-xs text-zinc-500 truncate">{cognitoUser.email}</p>
          )}
        </div>
      </div>

      <p className="text-xs text-zinc-500">
        Your conversations are saved permanently. Chat history survives browser
        refresh and new tabs.
      </p>

      <Button
        variant="destructive"
        className="w-full"
        onClick={onSignOut}
        disabled={loading}
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />
        ) : null}
        Sign out
      </Button>
    </div>
  );
}

/** Inline Google "G" logo SVG */
function GoogleIcon() {
  return (
    <svg
      className="h-4 w-4 shrink-0"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}
