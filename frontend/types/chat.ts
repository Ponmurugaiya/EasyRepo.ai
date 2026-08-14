// ─────────────────────────────────────────────────────────────────────────────
// Chat Types — client-side session state
// ─────────────────────────────────────────────────────────────────────────────

import type { AskResponse, ValidationReport } from "./api";

export type MessageRole = "user" | "assistant" | "error";

export interface UserMessage {
  id: string;
  role: "user";
  content: string;
  timestamp: number;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  content: string;
  timestamp: number;
  provider: string;
  citations: ValidationReport;
  context_entities: string[];
  loading?: false;
  /** True when the answer came from the hierarchical overview pipeline. */
  is_overview?: boolean;
}

export interface LoadingMessage {
  id: string;
  role: "assistant";
  content: "";
  timestamp: number;
  loading: true;
}

export interface ErrorMessage {
  id: string;
  role: "error";
  content: string;
  timestamp: number;
}

export type ChatMessage =
  | UserMessage
  | AssistantMessage
  | LoadingMessage
  | ErrorMessage;

export interface Conversation {
  id: string;
  repoId: string;
  repoName: string;
  repoUrl: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

export interface RepoSession {
  repoId: string;
  repoName: string;
  repoUrl: string;
  status: "pending" | "indexing" | "ready" | "failed";
  entityCount: number;
  relationshipCount: number;
  indexedAt: string | null;
  /** Human-readable progress or failure reason from the backend. */
  progressMessage?: string | null;
}
