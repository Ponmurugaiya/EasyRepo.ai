from typing import Literal, Optional
from pydantic import BaseModel, Field


class Entity(BaseModel):
    id: str
    type: Literal["module", "class", "interface", "function", "method", "doc_block"]
    name: str
    file_path: str
    start_line: int
    end_line: int
    parent_id: Optional[str] = None
    language: Literal["python", "typescript", "markdown"]
    has_docstring: bool
    source: str = ""


class Relationship(BaseModel):
    source_id: str
    target_id: str
    type: Literal["CONTAINS", "CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS"]
    file_path: str
    line: int
