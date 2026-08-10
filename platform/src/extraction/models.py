from typing import Literal, Optional
from pydantic import BaseModel, Field


Language = Literal["python", "typescript"]


class Entity(BaseModel):
    id: str
    type: Literal["module", "class", "interface", "function", "method", "doc_block", "variable"]
    name: str
    file_path: str
    start_line: int
    end_line: int
    parent_id: Optional[str] = None
    language: Language
    has_docstring: bool
    source: str = ""


class Relationship(BaseModel):
    source_id: str
    target_id: str
    type: Literal["CONTAINS", "CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "INSTANTIATES"]
    file_path: str
    line: int
