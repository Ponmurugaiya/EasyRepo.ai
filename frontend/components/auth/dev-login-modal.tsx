"use client";

import { useState } from "react";
import { useAuthStore } from "@/store/auth-store";
import { useChatStore } from "@/store/chat-store";
import { listRepositories } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { KeyRound, LogOut, ExternalLink, Copy, Check } from "lucide-react";
import { getHealth } from "@/lib/api";

interface DevLoginModalProps {
  open: boolean;
  onClose: () => void;
}

export function DevLoginModal({ open, onClose }: DevLoginModalProps) {
  const { token, isLoggedIn, setToken, clearToken } = useAuthStore();
  const { syncRepoSessions } = useChatStore();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [copied, setCopied] = useState(false);

  async function handleSave() {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError("Paste your API token first.");
      return;
    }
    if (!trimmed.startsWith("er_")) {
      setError("Token should start with 'er_'. Get one from POST /auth/register.");
      return;
    }

    // Quick connectivity check — the /auth/me endpoint returns profile when
    // the token is valid, and 401 when it isn't.
    setChecking(true);
    setError(null);
    try {
      // Temporarily stash the token so getDevToken() picks it up in the
      // request helper, then verify via /auth/me.
      setToken(trimmed);
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/me`,
        { headers: { "X-API-Key": trimmed } }
      );
      if (res.status === 401) {
        clearToken();
        setError("Token rejected by the server (401). Double-check the value.");
        return;
      }
      if (!res.ok) {
        setError(`Warning: server returned ${res.status}. Token saved anyway.`);
      }
      // Sync the sidebar — shows only repos this user can access, auto-grants
      // viewer access on any ready repos found in localStorage.
      try {
        const repos = await listRepositories();
        syncRepoSessions(repos);
      } catch {
        // Non-fatal — sidebar will still work, repos just won't be pruned
      }
      setDraft("");
      onClose();
    } catch {
      // Network error — save the token anyway so offline dev still works.
      setDraft("");
      onClose();
    } finally {
      setChecking(false);
    }
  }

  function handleLogout() {
    clearToken();
    setDraft("");
    setError(null);
    // Re-sync without a token → backend returns all repos (anonymous open access)
    listRepositories().then(syncRepoSessions).catch(() => {});
    onClose();
  }

  function copyRegisterCommand() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const cmd = `curl -s -X POST ${apiUrl}/auth/register -H "Content-Type: application/json" -d '{"email":"dev@local"}'`;
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-zinc-900 border-zinc-700 text-zinc-100 max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-white">
            <KeyRound className="h-4 w-4 text-blue-400" />
            Developer Login
          </DialogTitle>
          <DialogDescription className="text-zinc-400">
            Log in with a personal API token so your conversations are persisted
            in the database across browser refreshes.
          </DialogDescription>
        </DialogHeader>

        {isLoggedIn ? (
          <div className="space-y-4 pt-1">
            <div className="rounded-lg border border-green-700/40 bg-green-900/20 px-4 py-3">
              <p className="text-sm font-medium text-green-400">Logged in as developer</p>
              <p className="mt-1 text-xs text-zinc-500 font-mono break-all">{token}</p>
            </div>
            <p className="text-xs text-zinc-500">
              Conversation history is being saved to the database. Sessions
              survive browser refresh and new tabs.
            </p>
            <Button
              variant="destructive"
              className="w-full"
              onClick={handleLogout}
            >
              <LogOut className="h-3.5 w-3.5 mr-2" />
              Log out (switch to anonymous mode)
            </Button>
          </div>
        ) : (
          <div className="space-y-4 pt-1">
            {/* How to get a token */}
            <div className="rounded-lg border border-zinc-700 bg-zinc-800/60 px-4 py-3 space-y-2">
              <p className="text-xs font-medium text-zinc-300">
                Don't have a token yet?
              </p>
              <p className="text-xs text-zinc-500">
                Run this command once to create a developer account:
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-zinc-950 rounded px-2 py-1 text-blue-300 font-mono truncate">
                  POST /auth/register {"{"}"email":"dev@local"{"}"}
                </code>
                <button
                  onClick={copyRegisterCommand}
                  className="shrink-0 text-zinc-500 hover:text-zinc-300 transition-colors"
                  title="Copy curl command"
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5 text-green-400" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
              <a
                href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/docs#/auth/register_auth_register_post`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300"
              >
                Open in Swagger UI
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>

            {/* Token input */}
            <div className="space-y-1.5">
              <Label htmlFor="dev-token" className="text-xs text-zinc-400">
                Paste your API token
              </Label>
              <Input
                id="dev-token"
                type="password"
                placeholder="er_xxxxxxxxxxxxxxxx.xxxxxxxxx..."
                value={draft}
                onChange={(e) => {
                  setDraft(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => e.key === "Enter" && handleSave()}
                className="bg-zinc-800 border-zinc-700 text-zinc-100 placeholder:text-zinc-600 font-mono text-xs"
              />
              {error && <p className="text-xs text-red-400">{error}</p>}
            </div>

            <Button
              className="w-full bg-blue-600 hover:bg-blue-500"
              onClick={handleSave}
              disabled={checking || !draft.trim()}
            >
              {checking ? "Verifying…" : "Save token & log in"}
            </Button>

            <p className="text-xs text-zinc-600 text-center">
              Token stored in localStorage only — never sent anywhere except
              your local backend.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
