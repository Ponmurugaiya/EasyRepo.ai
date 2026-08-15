// ─────────────────────────────────────────────────────────────────────────────
// API Types — mirrors the FastAPI schemas exactly
// ─────────────────────────────────────────────────────────────────────────────

export type RepoStatus = "pending" | "indexing" | "ready" | "failed" | "cancelled";

export interface RepositoryResponse {
  repo_id: string;
  name: string;
  status: RepoStatus;
  url_or_path: string;
  entity_count: number;
  relationship_count: number;
  indexed_at: string | null;
  language_warning: string | null;
}

export interface RepositoryStatusResponse {
  repo_id: string;
  name: string;
  status: RepoStatus;
  indexed_at: string | null;
  progress_message: string | null;
  language_warning: string | null;
}

export interface CitationMatch {
  raw: string;
  file_path: string;
  start_line: number;
  end_line: number;
  matched_entity_id: string;
  matched_entity_name: string;
  citation_type: string;
  caller_entity_name: string | null;
  callee_entity_name: string | null;
}

export interface CitationMismatch {
  raw: string;
  file_path: string;
  start_line: number;
  end_line: number;
  reason: string;
  nearest_entity: string | null;
}

export interface ValidationReport {
  total_citations: number;
  definition_citations: CitationMatch[];
  call_site_citations: CitationMatch[];
  unsupported_citations: CitationMismatch[];
  hallucination_rate: number;
}

export interface AskResponse {
  answer: string;
  citations: ValidationReport;
  context_entities: string[];
  provider: string;
  /** True when the answer came from the hierarchical overview pipeline. */
  is_overview?: boolean;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export interface AskRequest {
  query: string;
  top_k?: number;
  model?: string;
  /** Stable UUID identifying the conversation thread (same value on every turn). */
  conversation_id?: string;
  /** Last N turns from the client — used for anonymous user context injection. */
  conversation_history?: ConversationTurn[];
}

export interface EntitySourceResponse {
  entity_id: string;
  name: string;
  file_path: string;
  start_line: number;
  end_line: number;
  language: string;
  source: string;
}

export interface RepositoryCreateRequest {
  source: string;
}

export interface ApiError {
  detail: string;
  error_code?: string;
}
