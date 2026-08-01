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


# ---------------------------------------------------------------------------
# Error schema
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error response schema."""

    detail: str
    error_code: Optional[str] = None
