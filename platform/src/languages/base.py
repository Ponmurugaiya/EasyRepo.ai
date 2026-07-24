"""Abstract LanguageAdapter interface and shared data-transfer objects.

All language-specific AST knowledge lives in concrete adapters.  The
extraction walker and every resolver depend ONLY on the types defined here.
Adding language N means writing a new adapter; nothing else changes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Iterator, Optional

import tree_sitter


# ---------------------------------------------------------------------------
# Shared data-transfer objects (adapter output, resolver input)
# ---------------------------------------------------------------------------


@dataclass
class ImportStatement:
    """Normalised representation of a single import statement.

    The adapter is responsible for turning language-specific import syntax
    into this common shape.  Resolvers consume it without knowing whether
    the source was Python ``from x import Y`` or TypeScript ``import { Y } from 'x'``.
    """

    module_path: str
    """Dotted or slash-separated module path, normalised by the adapter.

    Examples:
      Python ``from models.user import UserModel``  -> ``"models.user"``
      Python ``from .base import BaseModel``         -> ``".base"``  (is_relative=True)
      TypeScript ``import { X } from '../models/user.model'``
                                                     -> ``"../models/user.model"``
    """

    imported_names: list[str]
    """Bare names being imported.  Empty list means ``import whole_module``."""

    aliases: dict[str, str]
    """Mapping of *local_name* -> *original_name* for aliased imports.

    E.g. ``from x import Foo as Bar`` -> ``{"Bar": "Foo"}``.
    """

    is_relative: bool
    """True for Python relative imports (``from . import X``) and TypeScript
    relative path imports (``'./...'``, ``'../...'``)."""

    line: int
    """1-indexed source line of the import statement."""


@dataclass
class CallExpression:
    """Normalised representation of a single call site within a function body."""

    callee_name: str
    """Final method/function name being called (e.g. ``"validate"``)."""

    is_self_qualified: bool
    """True when the call is on ``self`` (Python) or ``this`` (TypeScript),
    e.g. ``self.auth_service.validate(...)``."""

    receiver_name: Optional[str]
    """Attribute name on self/this that holds the callee, if any.

    For ``self.auth_service.validate(...)``  -> ``"auth_service"``.
    For ``self.validate(...)``               -> ``None``.
    For ``UserModel(...)``                   -> ``None``.
    """

    line: int
    """1-indexed source line of the call expression."""


@dataclass
class EntityDecl:
    """Descriptor for one entity declaration yielded by ``iter_top_level_decls``
    or the recursive sub-walker inside a class/function body.

    The generic walker in *entity_extractor.py* converts these into full
    ``Entity`` objects and recurses into ``body_node``.
    """

    name: str
    entity_type: str          # "class" | "function" | "method" | "interface"
    start_line: int
    end_line: int
    has_docstring: bool
    source: str
    body_node: Optional[tree_sitter.Node]
    """The body child-node to recurse into (None for leaf declarations)."""
    is_class_scope: bool
    """Whether children of *body_node* are inside a class scope (methods)."""
    ast_node: Optional[tree_sitter.Node] = field(default=None, repr=False)
    """Reference to the raw AST node — used by resolvers."""


# ---------------------------------------------------------------------------
# Abstract adapter
# ---------------------------------------------------------------------------


class LanguageAdapter(abc.ABC):
    """Contract that every language plugin must satisfy.

    The entity walker and all resolvers call *only* these methods; they
    never inspect AST node types directly.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def language_name(self) -> str:
        """Human-readable language name, e.g. ``"python"``."""

    @property
    @abc.abstractmethod
    def file_extensions(self) -> list[str]:
        """File extensions handled by this adapter, e.g. ``['.py']``."""

    # ------------------------------------------------------------------
    # Parser bootstrap
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_tree_sitter_language(self) -> tree_sitter.Language:
        """Return the tree-sitter ``Language`` object for this language."""

    # ------------------------------------------------------------------
    # Entity extraction (Part A)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def has_module_docstring(self, root_node: tree_sitter.Node, source_bytes: bytes) -> bool:
        """Return ``True`` if the file-level node has a module docstring / JSDoc."""

    @abc.abstractmethod
    def iter_child_decls(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        is_inside_class: bool,
        rel_path: str,
    ) -> Iterator[EntityDecl]:
        """Yield entity declarations that are *direct* children of *node*.

        The generic walker calls this recursively.  The adapter must NOT
        recurse itself — it only yields the immediate children and
        provides ``body_node`` for the walker to descend into.

        Args:
            node: The parent AST node whose children to scan.
            source_bytes: Raw UTF-8 source bytes for byte-range slicing.
            is_inside_class: True when *node* is the body of a class.
            rel_path: Relative path of the file (used for interface detection).
        """

    # ------------------------------------------------------------------
    # Relationship extraction (Part B)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_imports(
        self, root_node: tree_sitter.Node, source_bytes: bytes, file_path: str
    ) -> list[ImportStatement]:
        """Parse and return all import statements in the file.

        Returns raw ``ImportStatement`` objects — NOT resolved to entity IDs.
        Resolution is the resolver's job.
        """

    @abc.abstractmethod
    def get_call_expressions(
        self, func_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[CallExpression]:
        """Return all call sites within *func_node*'s body.

        Only direct calls (not deeply nested lambdas / comprehensions that are
        out-of-scope) are required.
        """

    @abc.abstractmethod
    def get_inheritance_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Return raw base-class name strings from a class declaration.

        For Python: names from the argument_list of class_definition.
        For TypeScript: names from the extends_clause.

        These are raw identifier strings (e.g. ``"BaseModel"``); the
        inheritance resolver looks them up in the symbol table.
        """

    @abc.abstractmethod
    def get_implements_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Return raw interface/protocol name strings a class explicitly implements.

        For Python: empty list — Python has no ``implements`` keyword; ABC bases
        are returned by ``get_inheritance_bases`` and classified by the resolver
        via ``is_interface_like``.
        For TypeScript: names from the implements_clause.
        """

    @abc.abstractmethod
    def is_interface_like(self, entity_type: str, file_path: str) -> bool:
        """Return ``True`` if an entity with the given type/path should be treated
        as an interface (target of IMPLEMENTS rather than INHERITS).

        Args:
            entity_type: The ``Entity.type`` string (``"interface"``, ``"class"``, …).
            file_path: The entity's ``file_path`` (used for Python path-based heuristic).
        """
