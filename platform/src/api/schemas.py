"""Pydantic request/response models for FastAPI endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Repository schemas
# ---------------------------------------------------------------------------


class RepositoryCreateRequest(BaseModel):
    """Request model for creating/ingesting a repository."""

    source: str = Field(
        ...,
        description="Local path or Git URL to the repository",
        min_length=1,
        max_length=500,
        examples=["/path/to/repo", "https://github.com/user/repo"],
    )

    @field_validator("source")
    @classmethod
    def no_path_traversal(cls, v: str) -> str:
        """Reject obvious path traversal attempts in local paths."""
        if ".." in v.split("/") or ".." in v.split("\\"):
            raise ValueError("Path traversal sequences ('..') are not allowed in source.")
        return v


class RepositoryResponse(BaseModel):
    """Response model for repository metadata."""

    repo_id: str
    name: str
    status: str
    url_or_path: str
    entity_count: Optional[int] = 0
    relationship_count: Optional[int] = 0
    indexed_at: Optional[str] = None

    model_config = {"from_attributes": True}


class RepositoryStatusResponse(BaseModel):
    """Response model for repository status endpoint.

    Clients should poll this endpoint after ``POST /repositories`` returns
    ``202 Accepted``.  ``status`` transitions:
    ``pending`` → ``indexing`` → ``ready`` | ``failed``.
    """

    repo_id: str
    name: str
    status: str
    indexed_at: Optional[str] = None
    progress_message: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Retrieval-only schemas
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Request model for retrieval-only query endpoint."""

    query: str = Field(..., description="Natural language query", min_length=1, max_length=2000)
    top_k: int = Field(10, description="Number of top results to retrieve", ge=1, le=100)


class QueryResponse(BaseModel):
    """Response model for retrieval-only query endpoint."""

    entities: List[Dict[str, Any]]
    execution_traces: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]
    rendered_text: str
    total_tokens_est: int
    truncated: bool


# ---------------------------------------------------------------------------
# Ask (full pipeline) schemas
# ---------------------------------------------------------------------------


class ConversationTurn(BaseModel):
    """A single turn in a conversation (user or assistant message)."""

    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message text")


class AskRequest(BaseModel):
    """Request model for full pipeline ask endpoint."""

    query: str = Field(..., description="Natural language question", min_length=1, max_length=2000)
    top_k: int = Field(10, description="Number of top results to retrieve", ge=1, le=100)
    # Optional model override — if omitted the server picks (Groq rotation → Gemini)
    model: Optional[str] = Field(
        None,
        description=(
            "LLM model identifier to use.  Prefix with 'groq:' to force a specific "
            "Groq model (e.g. 'groq:llama3-70b-8192'), or 'gemini:' for Gemini.  "
            "Omit to use the default provider cascade (Groq → Gemini)."
        ),
    )
    # LTM scoping — optional, no-op when absent (backward compatible)
    session_id: Optional[str] = Field(
        None,
        description="Client-generated UUID scoping long-term memory reads/writes to a session.",
    )
    # Conversation history — stable UUID per conversation thread
    conversation_id: Optional[str] = Field(
        None,
        description="Stable UUID identifying the conversation thread (same value on every turn).",
    )
    # Conversation history — last N turns sent by the client for context injection
    conversation_history: List[ConversationTurn] = Field(
        default_factory=list,
        description="Last N user/assistant turns from the client (used for anonymous users).",
    )


class CitationMatchSchema(BaseModel):
    """Schema for a verified citation."""

    raw: str
    file_path: str
    start_line: int
    end_line: int
    matched_entity_id: str
    matched_entity_name: str
    citation_type: str
    caller_entity_name: Optional[str] = None
    callee_entity_name: Optional[str] = None


class CitationMismatchSchema(BaseModel):
    """Schema for an unsupported citation."""

    raw: str
    file_path: str
    start_line: int
    end_line: int
    reason: str
    nearest_entity: Optional[str] = None
    nearest_entity_id: Optional[str] = None


class ValidationReportSchema(BaseModel):
    """Schema for citation validation report."""

    total_citations: int
    definition_citations: List[CitationMatchSchema]
    call_site_citations: List[CitationMatchSchema]
    unsupported_citations: List[CitationMismatchSchema]
    hallucination_rate: float


class AskResponse(BaseModel):
    """Response model for full pipeline ask endpoint."""

    answer: str
    citations: ValidationReportSchema
    context_entities: List[str]
    # Provider used: "groq" or "gemini"
    provider: str = "unknown"
    # True when the answer came from the hierarchical overview pipeline.
    # Frontend uses this to suppress inline citation badges and show only
    # the citation panel listing all files used.
    is_overview: bool = False


class AskJobSubmittedResponse(BaseModel):
    """Returned immediately by POST /ask — contains the job ID to poll."""

    job_id: str
    status: str = "pending"


class AskJobProgressSchema(BaseModel):
    """Live pipeline progress written by the worker while status=running.

    pipeline:
        "overview"  — repository_overview / repository_detailed intent
        "semantic"  — all other intents (semantic search)
    stage:
        "classifying"   — query planner running
        "reading_files" — overview: file summarisation in progress
        "insights"      — overview: folder agents aggregating file summaries
        "searching"     — semantic: vector search + graph expansion
        "generating"    — LLM generating the final answer
        "citations"     — citation validation + correction
    files_done / files_total:
        Only meaningful when stage == "reading_files".
    """

    pipeline: str = "semantic"   # "overview" | "semantic"
    stage: str = "classifying"
    files_done: int = 0
    files_total: int = 0


class AskJobStatusResponse(BaseModel):
    """Returned by GET /ask/{job_id} — either pending/running or the full result."""

    job_id: str
    status: str  # "pending" | "running" | "done" | "failed"
    result: Optional[AskResponse] = None
    error: Optional[str] = None
    # Present while status is "running" (None when pending/done/failed)
    progress: Optional[AskJobProgressSchema] = None


# ---------------------------------------------------------------------------
# Error schema
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error response schema."""

    detail: str
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Graph schemas
# ---------------------------------------------------------------------------


class EntityConnectionSchema(BaseModel):
    """One entity-level connection behind a file-level edge."""

    from_entity_id: str
    from_entity_name: str
    to_entity_id: str
    to_entity_name: str
    rel_type: str
    line: int


class FileEdgeSchema(BaseModel):
    """Aggregated cross-file relationship edge."""

    source_file_id: str
    target_file_id: str
    rel_types: List[str]
    dominant_type: str
    connections: List[EntityConnectionSchema]


class InlineEntitySchema(BaseModel):
    """An entity embedded directly inside a FileNodeSchema."""

    id: str
    name: str
    type: str
    start_line: int
    end_line: int
    has_docstring: bool


class FileNodeSchema(BaseModel):
    """A file (module entity) as a graph node — entities + full source embedded."""

    id: str
    file_path: str
    name: str
    language: str
    is_entry: bool
    entry_score: int
    depth: int = 0
    is_root: bool = False
    source: str = ""                          # full file source text
    entities: List["InlineEntitySchema"] = Field(default_factory=list)


# Resolve forward reference
FileNodeSchema.model_rebuild()


class FileGraphResponse(BaseModel):
    """Response for the file-level graph endpoint."""

    root: Optional[str]
    entry_points: List[str]        # entity IDs, ranked by score
    nodes: List[FileNodeSchema]
    edges: List[FileEdgeSchema]


class ExpandedEntitySchema(BaseModel):
    """An entity inside an expanded file node."""

    id: str
    name: str
    type: str
    start_line: int
    end_line: int
    language: str
    has_docstring: bool


class FileExpandResponse(BaseModel):
    """Response for the expand endpoint — entities inside one file."""

    file_id: str
    file_path: str
    entities: List[ExpandedEntitySchema]
    outgoing_edges: List[EntityConnectionSchema]
    incoming_edges: List[EntityConnectionSchema]
