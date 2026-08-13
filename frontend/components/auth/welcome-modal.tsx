"use client";

import { useState } from "react";
import { useAuthStore } from "../../store/auth-store";
import { useChatStore } from "../../store/chat-store";
import { listRepositories } from "../../lib/api";
import { Button } from "../../components/ui/button";
import { Bot, Loader2, Clock } from "lucide-react";

export function WelcomeModal() {
  const { hasSeenWelcome, dismissWelcome, setCognitoUser } = useAuthStore();
  const { syncRepoSessions } = useChatStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already dismissed — render nothing
  if (hasSeenWelcome) return null;

  async function handleGoogleSignIn() {
    setLoading(true);
    setError(null);
    try {
      const { configureCognito, signInWithGoogle } = await import("../../lib/cognito");
      const configured = configureCognito();
      if (!configured) {
        setError("Cognito is not configured yet.");
        setLoading(false);
        return;
      }
      await signInWithGoogle();
      // Page will redirect — nothing else to do
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
      setLoading(false);
    }
  }

  async function handleTemporary() {
    dismissWelcome();
    try {
      const repos = await listRepositories();
      syncRepoSessions(repos);
    } catch {
      // non-fatal
    }
  }

  return (
    /* Backdrop */
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Blurred background */}
      <div className="absolute inset-0 bg-zinc-950/70 backdrop-blur-sm" />

      {/* Card */}
      <div className="relative w-full max-w-sm rounded-2xl border border-zinc-700 bg-zinc-900 shadow-2xl p-8 flex flex-col items-center text-center gap-6">

        {/* Logo */}
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-blue-600/20 border border-blue-600/30">
          <Bot className="h-8 w-8 text-blue-400" />
        </div>

        {/* Heading */}
        <div className="space-y-1.5">
          <h1 className="text-xl font-bold text-white">Welcome to EasyRepo</h1>
          <p className="text-sm text-zinc-400 leading-relaxed">
            AI-powered codebase intelligence. Sign in to save your history, or
            start exploring right away.
          </p>
        </div>

        {/* Actions */}
        <div className="w-full space-y-3">
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

          <button
            onClick={handleTemporary}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 rounded-lg border border-zinc-700 px-4 py-2.5 text-sm text-zinc-400 hover:text-zinc-200 hover:border-zinc-600 hover:bg-zinc-800/50 transition-colors disabled:opacity-50"
          >
            <Clock className="h-4 w-4 shrink-0" />
            Continue as guest
          </button>
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <p className="text-xs text-zinc-600 leading-relaxed">
          Guest sessions are temporary and won't be saved across refreshes.
        </p>
      </div>
    </div>
  );
}

/** Inline Google "G" logo SVG */
function GoogleIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  );
}
