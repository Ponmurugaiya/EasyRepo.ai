"""Deterministic Tree-sitter Entity Extractor.

ID SCHEME SPECIFICATION:
========================
- Python Modules:
  Replaces 'python/' prefix with 'py.', strips '.py', converts slashes '/' to '.'.
  Example: 'python/models/base.py' -> 'py.models.base'

- TypeScript Modules:
  Replaces 'typescript/' prefix with 'ts.', strips '.ts'/'.tsx', converts '.' in base name to '_',
  and converts slashes '/' to '.'.
  Example: 'typescript/models/user.model.ts' -> 'ts.models.user_model'
  Example: 'typescript/index.ts' -> 'ts.index'

- Markdown Modules:
  Strips '.md', converts slashes '/' to '.', converts to lowercase.
  Example: 'docs/ARCHITECTURE.md' -> 'docs.architecture'
  Example: 'README.md' -> 'readme'

- Child Entities (Classes, Interfaces, Functions, Methods):
  Appending the entity's name to its parent ID: '<parent_id>.<entity_name>'.
  Example: 'py.services.auth_service.AuthService.authenticate_user'

ARCHITECTURE NOTE:
==================
This module is language-agnostic.  All AST node-type knowledge lives in
``src.languages.*Adapter`` classes.  Adding a new language means writing a
new adapter and registering it in ``src.languages.__init__``; this file
requires no changes.
"""

import os
from pathlib import Path
from typing import List, Tuple, Optional

import tree_sitter
from src.extraction.models import Entity, Relationship, Language
from src.languages import get_adapter, ADAPTER_REGISTRY
from src.languages.base import LanguageAdapter


# Languages not driven by adapters (no tree-sitter grammar needed)
_PASSTHROUGH_LANGUAGES: dict[str, Language] = {
    ".md": "markdown",
}


class EntityExtractor:
    """Extracts code entities and CONTAINS relationships from source repositories.

    Uses registered ``LanguageAdapter`` instances — no language-specific logic
    lives here.
    """

    def __init__(self) -> None:
        # Build language -> tree-sitter parser table from registered adapters
        import tree_sitter as _ts
        self._parsers: dict[str, _ts.Parser] = {}
        for ext, adapter in ADAPTER_REGISTRY.items():
            lang_name = adapter.language_name
            if lang_name not in self._parsers:
                lang = adapter.get_tree_sitter_language()
                self._parsers[lang_name] = _ts.Parser(lang)

    # ------------------------------------------------------------------
    # Module ID generation (language-neutral path manipulation)
    # ------------------------------------------------------------------

    def generate_module_id(self, relative_path: str, language: Language) -> str:
        p = Path(relative_path).as_posix()
        if language == "python":
            if p.startswith("python/"):
                p = p[len("python/"):]
            if p.endswith(".py"):
                p = p[:-3]
            return "py." + p.replace("/", ".")
        elif language == "typescript":
            if p.startswith("typescript/"):
                p = p[len("typescript/"):]
            if p.endswith(".ts"):
                p = p[:-3]
            elif p.endswith(".tsx"):
                p = p[:-4]
            parts = p.split("/")
            parts[-1] = parts[-1].replace(".", "_")
            return "ts." + ".".join(parts)
        elif language == "markdown":
            p_no_ext = os.path.splitext(p)[0]
            return p_no_ext.replace("/", ".").lower()
        else:
            p_no_ext = os.path.splitext(p)[0]
            return p_no_ext.replace("/", ".").lower()

    # ------------------------------------------------------------------
    # Repository walk
    # ------------------------------------------------------------------

    def extract_repository(
        self, repo_path: str
    ) -> Tuple[List[Entity], List[Relationship]]:
        repo_dir = Path(repo_path).resolve()
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d not in ("node_modules", "venv", "__pycache__", "platform")
            ]
            for file in sorted(files):
                full_path = Path(root) / file
                rel_path = full_path.relative_to(repo_dir).as_posix()
                suffix = Path(file).suffix

                adapter = get_adapter(suffix)
                language: Optional[Language] = None

                if adapter is not None:
                    language = adapter.language_name  # type: ignore[assignment]
                elif suffix in _PASSTHROUGH_LANGUAGES:
                    language = _PASSTHROUGH_LANGUAGES[suffix]
                else:
                    continue

                file_ents, file_rels = self.extract_file(
                    str(full_path), rel_path, language, adapter
                )
                entities.extend(file_ents)
                relationships.extend(file_rels)

        return entities, relationships

    @property
    def parser(self):
        """Backward compatibility helper for tests expecting extractor.parser."""
        return next(iter(self._parsers.values()), None)

    # ------------------------------------------------------------------
    # Single-file extraction
    # ------------------------------------------------------------------

    def extract_file(
        self,
        full_path: str,
        rel_path: str,
        language: Language,
        adapter: Optional[LanguageAdapter] = None,
    ) -> Tuple[List[Entity], List[Relationship]]:
        if adapter is None:
            adapter = get_adapter(Path(rel_path).suffix)

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            source_text = f.read()

        source_lines = source_text.split("\n")
        total_lines = max(len(source_lines), 1)
        module_id = self.generate_module_id(rel_path, language)
        file_name = Path(rel_path).name

        module_entity = Entity(
            id=module_id,
            type="module",
            name=file_name,
            file_path=rel_path,
            start_line=1,
            end_line=total_lines,
            parent_id=None,
            language=language,
            has_docstring=True if language == "markdown" else False,
            source=source_text,
        )

        entities: List[Entity] = [module_entity]
        relationships: List[Relationship] = []

        # Markdown and other passthrough languages — no AST
        if adapter is None:
            return entities, relationships

        source_bytes = source_text.encode("utf-8")
        parser = self._parsers.get(adapter.language_name)
        if parser is None:
            return entities, relationships

        tree = parser.parse(source_bytes)
        if not tree:
            return entities, relationships

        root_node = tree.root_node
        module_entity.has_docstring = adapter.has_module_docstring(root_node, source_bytes)

        # Generic recursive walk — zero language-specific logic
        child_ents, child_rels = self._walk(
            adapter=adapter,
            node=root_node,
            source_bytes=source_bytes,
            parent_id=module_id,
            language=language,
            rel_path=rel_path,
            is_inside_class=False,
        )

        entities.extend(child_ents)
        relationships.extend(child_rels)

        return entities, relationships

    # ------------------------------------------------------------------
    # Generic recursive walker
    # ------------------------------------------------------------------

    def _walk(
        self,
        adapter: LanguageAdapter,
        node: tree_sitter.Node,
        source_bytes: bytes,
        parent_id: str,
        language: Language,
        rel_path: str,
        is_inside_class: bool,
    ) -> Tuple[List[Entity], List[Relationship]]:
        """Language-agnostic recursive walker.

        Calls ``adapter.iter_child_decls()`` for each level and recurses into
        ``decl.body_node`` when present.  All language-specific decisions
        (what counts as a class, method, etc.) are inside the adapter.
        """
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        for decl in adapter.iter_child_decls(
            node, source_bytes, is_inside_class, rel_path
        ):
            ent_id = f"{parent_id}.{decl.name}"

            ent = Entity(
                id=ent_id,
                type=decl.entity_type,  # type: ignore[arg-type]
                name=decl.name,
                file_path=rel_path,
                start_line=decl.start_line,
                end_line=decl.end_line,
                parent_id=parent_id,
                language=language,
                has_docstring=decl.has_docstring,
                source=decl.source,
            )
            entities.append(ent)
            relationships.append(
                Relationship(
                    source_id=parent_id,
                    target_id=ent_id,
                    type="CONTAINS",
                    file_path=rel_path,
                    line=decl.start_line,
                )
            )

            if decl.body_node is not None:
                sub_ents, sub_rels = self._walk(
                    adapter=adapter,
                    node=decl.body_node,
                    source_bytes=source_bytes,
                    parent_id=ent_id,
                    language=language,
                    rel_path=rel_path,
                    is_inside_class=decl.is_class_scope,
                )
                entities.extend(sub_ents)
                relationships.extend(sub_rels)

        return entities, relationships
