"use client";

import { useState } from "react";
import { Info, X } from "lucide-react";
import { cn } from "../../lib/utils";

interface GuestBannerProps {
  onSignIn: () => void;
  /** Show the stricter "1 question used" variant */
  questionUsed?: boolean;
  className?: string;
}

export function GuestBanner({
  onSignIn,
  questionUsed = false,
  className,
}: GuestBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  return (
    <div
      className={cn(
        "flex items-center gap-2 px-4 py-2 text-xs border-b",
        questionUsed
          ? "bg-amber-950/40 border-amber-800/40 text-amber-300"
          : "bg-zinc-900/60 border-zinc-800 text-zinc-400",
        className
      )}
      role="status"
      aria-live="polite"
    >
      <Info className="h-3.5 w-3.5 shrink-0" />

      {questionUsed ? (
        <span className="flex-1">
          You've used your free question.{" "}
          <button
            onClick={onSignIn}
            className="underline underline-offset-2 text-amber-200 hover:text-white transition-colors"
          >
            Sign in
          </button>{" "}
          to ask follow-up questions and save your history.
        </span>
      ) : (
        <span className="flex-1">
          Temporary session — your chat will be erased when you leave.{" "}
          <button
            onClick={onSignIn}
            className="underline underline-offset-2 text-zinc-300 hover:text-white transition-colors"
          >
            Sign in
          </button>{" "}
          to save it permanently.
        </span>
      )}

      {!questionUsed && (
        <button
          onClick={() => setDismissed(true)}
          className="shrink-0 text-zinc-500 hover:text-zinc-300 transition-colors ml-1"
          aria-label="Dismiss banner"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
