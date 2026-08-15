// ─────────────────────────────────────────────────────────────────────────────
// Friendly error messages — single source of truth for all user-facing copy.
//
// Maps ApiError (HTTP status + optional error_code from backend) to a human
// message that hides implementation details from users.
// ─────────────────────────────────────────────────────────────────────────────

import { ApiError } from "./api";

/**
 * Map an ApiError to a friendly, user-facing message.
 * Checks error_code first (specific), then HTTP status (broad).
 */
export function friendlyMessage(err: ApiError): string {
  // ── Specific error codes emitted by the backend ──────────────────────────
  switch (err.errorCode) {
    case "llm_quota_exhausted":
      return "Our AI providers are currently over capacity. Please try again in a few minutes.";

    case "llm_rate_limited":
      return "We're receiving a lot of requests right now. Please wait a moment and try again.";

    case "llm_auth_error":
    case "jina_auth_error":
      return "There's a configuration issue on our end. Please contact support if this persists.";

    case "jina_rate_limited":
      return "The embedding service is temporarily rate limited. Please try again shortly.";

    case "repo_not_ready":
      return "This repository is still being indexed. Please wait for it to finish before asking questions.";

    case "repo_not_found":
      return "Repository not found. It may have been removed.";

    case "pipeline_error":
      return "Something went wrong while processing your question. Please try again.";
  }

  // ── HTTP status fallbacks ────────────────────────────────────────────────
  switch (err.status) {
    case 400:
      return "This action can't be completed right now. The repository may still be indexing.";
    case 401:
      return "Please sign in to continue.";
    case 403:
      return "You don't have access to this resource.";
    case 404:
      return "Repository not found.";
    case 408:
      return "The request took too long. Please try again.";
    case 429:
      return "Too many requests. Please slow down and try again in a moment.";
    case 500:
      return "Something went wrong on our end. Please try again.";
    case 503:
      return "The service is temporarily unavailable. Please try again shortly.";
  }

  // ── Generic fallback ────────────────────────────────────────────────────
  return "Something went wrong. Please try again.";
}

/**
 * Friendly message for repository indexing failures shown in the sidebar.
 * Translates raw pipeline error strings into clean copy.
 */
export function friendlyIngestError(rawMessage: string | null | undefined): string {
  if (!rawMessage) return "Indexing failed. Try re-indexing the repository.";

  const msg = rawMessage.toLowerCase();

  if (msg.includes("rate limit") || msg.includes("429") || msg.includes("too many")) {
    return "Indexing paused — embedding service rate limit reached. Try re-indexing shortly.";
  }
  if (msg.includes("jina") && (msg.includes("auth") || msg.includes("401") || msg.includes("invalid"))) {
    return "Indexing failed — Jina AI key is invalid or missing. Check your configuration.";
  }
  if (msg.includes("git clone") || msg.includes("clone failed")) {
    return "Could not clone the repository. Check the URL and try again.";
  }
  if (msg.includes("does not exist") || msg.includes("not found")) {
    return "Repository path not found. Check the URL or local path.";
  }
  if (msg.includes("timeout") || msg.includes("timed out")) {
    return "Indexing timed out. The repository may be too large — try again.";
  }

  return "Indexing failed. Try re-indexing the repository.";
}
