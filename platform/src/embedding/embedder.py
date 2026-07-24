"""Code entity embedding module using SentenceTransformers."""

from __future__ import annotations

import re
from typing import Any

from sentence_transformers import SentenceTransformer

from src.embedding.config import BATCH_SIZE, MODEL_NAME


def extract_docstring(source: str, has_docstring: bool) -> str:
    """Extract docstring text from entity source code if present."""
    if not has_docstring or not source:
        return ""

    # Python docstrings (triple double or triple single quotes)
    py_match = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', source, re.DOTALL)
    if py_match:
        doc = py_match.group(1) if py_match.group(1) is not None else py_match.group(2)
        return (doc or "").strip()

    # JS/TS JSDoc block comment /** ... */
    ts_match = re.search(r'/\*\*(.*?)\*/', source, re.DOTALL)
    if ts_match:
        lines = ts_match.group(1).split("\n")
        cleaned = [re.sub(r"^\s*\*?\s?", "", line) for line in lines]
        return "\n".join(cleaned).strip()

    return ""


def format_entity_for_embedding(entity: Any) -> str:
    """Format an entity into text for vector embedding generation.

    Template:
    Type: {type}
    Name: {name}
    Docstring: {docstring}

    {source}
    """
    if isinstance(entity, dict):
        ent_type = entity.get("type", "")
        name = entity.get("name", "")
        has_doc = entity.get("has_docstring", False)
        source = entity.get("source", "")
    else:
        ent_type = getattr(entity, "type", "")
        name = getattr(entity, "name", "")
        has_doc = getattr(entity, "has_docstring", False)
        source = getattr(entity, "source", "")

    docstring = extract_docstring(source, has_doc)
    return f"Type: {ent_type}\nName: {name}\nDocstring: {docstring}\n\n{source}"


class CodeEmbedder:
    """Embedder for code entities and natural language queries using SentenceTransformers."""

    def __init__(self, model_name: str = MODEL_NAME):
        self.model_name = model_name
        self._model: SentenceTransformer | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            # trust_remote_code=True is required for jinaai/jina-embeddings-v2-base-code
            # because it ships a custom ALiBi-based JinaBERT implementation alongside
            # its weights on the Hugging Face Hub.
            self._model = SentenceTransformer(self.model_name, trust_remote_code=True)
        return self._model

    def embed(self, text: str) -> list[float]:
        """Embed a single text string returning float vector."""
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(
        self, texts: list[str], batch_size: int = BATCH_SIZE
    ) -> list[list[float]]:
        """Embed a batch of text strings returning list of float vectors."""
        if not texts:
            return []
        embeddings = self.model.encode(
            texts, batch_size=batch_size, convert_to_numpy=True
        )
        return [vec.tolist() for vec in embeddings]
