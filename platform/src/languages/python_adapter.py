"""Python language adapter.

Implements ``LanguageAdapter`` for Python source files.  All Python-specific
AST node-type knowledge lives here — the walker and resolvers are unaware of
Python internals.

Node-type map reference (tree-sitter-python):
  class_definition        -> "class" (or "interface" for files under interfaces/)
  function_definition     -> "function" (or "method" when inside a class)
  decorated_definition    -> unwrapped to the inner class/function
"""

from __future__ import annotations

import re
from typing import Iterator, Optional

import tree_sitter
import tree_sitter_python

from src.languages.base import (
    CallExpression,
    EntityDecl,
    ImportStatement,
    LanguageAdapter,
)


class PythonAdapter(LanguageAdapter):
    """Language adapter for Python (.py) files."""

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def language_name(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return [".py"]

    # ------------------------------------------------------------------
    # Parser bootstrap
    # ------------------------------------------------------------------

    def get_tree_sitter_language(self) -> tree_sitter.Language:
        return tree_sitter.Language(tree_sitter_python.language())

    # ------------------------------------------------------------------
    # Entity extraction helpers
    # ------------------------------------------------------------------

    def has_module_docstring(
        self, root_node: tree_sitter.Node, source_bytes: bytes
    ) -> bool:
        return self._has_docstring(root_node, source_bytes)

    def _has_docstring(self, node: tree_sitter.Node, source_bytes: bytes) -> bool:
        """Return True if *node* (or its body) starts with a string literal."""
        body = (
            node.child_by_field_name("body")
            if node.type in ("function_definition", "class_definition")
            else node
        )
        if not body:
            return False
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        return True
                return False
            elif child.type in ("comment", "line_comment"):
                continue
            elif child.type in (
                "import_statement",
                "import_from_statement",
                "pass_statement",
            ):
                continue
            else:
                break
        return False

    @staticmethod
    def _get_line_range(node: tree_sitter.Node) -> tuple[int, int]:
        start_l = node.start_point[0] + 1
        end_l = node.end_point[0] + 1
        if node.end_point[1] == 0 and end_l > start_l:
            end_l -= 1
        return start_l, end_l

    def iter_child_decls(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        is_inside_class: bool,
        rel_path: str,
    ) -> Iterator[EntityDecl]:
        """Yield ``EntityDecl`` for every class/function/method that is a
        direct child of *node*, handling ``decorated_definition`` wrappers."""

        is_interface_file = "interfaces" in rel_path

        for child in node.children:
            # ----------------------------------------------------------------
            # decorated_definition  (@decorator\n def foo / class Foo)
            # ----------------------------------------------------------------
            if child.type == "decorated_definition":
                outer = child
                inner = outer.child_by_field_name("definition")
                if not inner:
                    for sub in outer.children:
                        if sub.type in ("function_definition", "class_definition"):
                            inner = sub
                            break
                if not inner:
                    continue

                name_node = inner.child_by_field_name("name")
                if not name_node:
                    continue

                name = source_bytes[name_node.start_byte:name_node.end_byte].decode()
                start_l, end_l = self._get_line_range(outer)

                if inner.type == "class_definition":
                    ent_type = "interface" if is_interface_file else "class"
                else:
                    ent_type = "method" if is_inside_class else "function"

                has_doc = self._has_docstring(inner, source_bytes)
                src = source_bytes[outer.start_byte:outer.end_byte].decode("utf-8", errors="replace")
                body = inner.child_by_field_name("body")

                yield EntityDecl(
                    name=name,
                    entity_type=ent_type,
                    start_line=start_l,
                    end_line=end_l,
                    has_docstring=has_doc,
                    source=src,
                    body_node=body,
                    is_class_scope=(ent_type in ("class", "interface")),
                    ast_node=inner,
                )

            # ----------------------------------------------------------------
            # class_definition
            # ----------------------------------------------------------------
            elif child.type == "class_definition":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue

                name = source_bytes[name_node.start_byte:name_node.end_byte].decode()
                start_l, end_l = self._get_line_range(child)
                ent_type = "interface" if is_interface_file else "class"
                has_doc = self._has_docstring(child, source_bytes)
                src = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                body = child.child_by_field_name("body")

                yield EntityDecl(
                    name=name,
                    entity_type=ent_type,
                    start_line=start_l,
                    end_line=end_l,
                    has_docstring=has_doc,
                    source=src,
                    body_node=body,
                    is_class_scope=True,
                    ast_node=child,
                )

            # ----------------------------------------------------------------
            # function_definition
            # ----------------------------------------------------------------
            elif child.type == "function_definition":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue

                name = source_bytes[name_node.start_byte:name_node.end_byte].decode()
                start_l, end_l = self._get_line_range(child)
                ent_type = "method" if is_inside_class else "function"
                has_doc = self._has_docstring(child, source_bytes)
                src = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                body = child.child_by_field_name("body")

                yield EntityDecl(
                    name=name,
                    entity_type=ent_type,
                    start_line=start_l,
                    end_line=end_l,
                    has_docstring=has_doc,
                    source=src,
                    body_node=body,
                    is_class_scope=is_inside_class,
                    ast_node=child,
                )

            # ----------------------------------------------------------------
            # anything else — recurse transparently (e.g. if_block at module level)
            # ----------------------------------------------------------------
            else:
                if child.type not in (
                    "class_definition",
                    "function_definition",
                    "decorated_definition",
                ):
                    yield from self.iter_child_decls(
                        child, source_bytes, is_inside_class, rel_path
                    )

    # ------------------------------------------------------------------
    # Relationship extraction (Part B)
    # ------------------------------------------------------------------

    def get_imports(
        self,
        root_node: tree_sitter.Node,
        source_bytes: bytes,
        file_path: str,
    ) -> list[ImportStatement]:
        """Parse Python import statements into normalised ``ImportStatement`` objects."""
        results: list[ImportStatement] = []

        for node in self._walk_flat(root_node):
            # from X import Y [as Z], ...
            if node.type == "import_from_statement":
                module_path, is_relative = self._parse_from_module(node, source_bytes)
                imported_names: list[str] = []
                aliases: dict[str, str] = {}
                line = node.start_point[0] + 1

                for child in node.children:
                    if child.type == "dotted_name":
                        # module already captured via _parse_from_module — skip first occurrence
                        continue
                    if child.type == "aliased_import":
                        orig_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if orig_node and alias_node:
                            orig = source_bytes[orig_node.start_byte:orig_node.end_byte].decode()
                            alias = source_bytes[alias_node.start_byte:alias_node.end_byte].decode()
                            imported_names.append(alias)
                            aliases[alias] = orig
                    if child.type in ("identifier", "dotted_name"):
                        # imported name (not the module part)
                        text = source_bytes[child.start_byte:child.end_byte].decode()
                        if text not in imported_names:
                            imported_names.append(text)

                # Re-parse children more carefully
                imported_names, aliases = self._parse_imported_names(node, source_bytes)

                results.append(
                    ImportStatement(
                        module_path=module_path,
                        imported_names=imported_names,
                        aliases=aliases,
                        is_relative=is_relative,
                        line=line,
                    )
                )

            # import X [as Y], ...
            elif node.type == "import_statement":
                line = node.start_point[0] + 1
                for child in node.children:
                    if child.type == "dotted_name":
                        module = source_bytes[child.start_byte:child.end_byte].decode()
                        results.append(
                            ImportStatement(
                                module_path=module,
                                imported_names=[],
                                aliases={},
                                is_relative=False,
                                line=line,
                            )
                        )
                    elif child.type == "aliased_import":
                        orig_node = child.child_by_field_name("name")
                        alias_node = child.child_by_field_name("alias")
                        if orig_node:
                            module = source_bytes[orig_node.start_byte:orig_node.end_byte].decode()
                            alias = (
                                source_bytes[alias_node.start_byte:alias_node.end_byte].decode()
                                if alias_node
                                else module
                            )
                            results.append(
                                ImportStatement(
                                    module_path=module,
                                    imported_names=[],
                                    aliases={alias: module},
                                    is_relative=False,
                                    line=line,
                                )
                            )

        return results

    def _parse_from_module(
        self, node: tree_sitter.Node, source_bytes: bytes
    ) -> tuple[str, bool]:
        """Extract the module path and is_relative flag from a from-import node.

        tree-sitter-python represents ``from .base import X`` as:
          import_from_statement
            "from"
            relative_import          <- this node, NOT a plain "."
              import_prefix
                "."                  <- dots live here
              dotted_name / identifier
            "import"
            ...
        We handle both the old flat structure and the nested relative_import.
        """
        dots = 0
        module_parts: list[str] = []
        past_from = False

        for child in node.children:
            text = source_bytes[child.start_byte:child.end_byte].decode()
            if text == "from":
                past_from = True
                continue
            if text == "import":
                break
            if not past_from:
                continue

            if child.type == "relative_import":
                # Nested structure: relative_import > import_prefix > "."
                # and optionally a dotted_name sibling inside relative_import
                for sub in child.children:
                    if sub.type == "import_prefix":
                        prefix_text = source_bytes[sub.start_byte:sub.end_byte].decode()
                        dots = len(prefix_text)  # count dots directly from text
                    elif sub.type in ("dotted_name", "identifier"):
                        module_parts.append(
                            source_bytes[sub.start_byte:sub.end_byte].decode()
                        )
            elif child.type == ".":
                # Flat structure (older grammars)
                dots += 1
            elif child.type in ("dotted_name", "identifier"):
                module_parts.append(text)

        module_path = "." * dots + ".".join(module_parts)
        return module_path, dots > 0

    def _parse_imported_names(
        self, node: tree_sitter.Node, source_bytes: bytes
    ) -> tuple[list[str], dict[str, str]]:
        """Extract the list of imported names and aliases from a from-import node."""
        names: list[str] = []
        aliases: dict[str, str] = {}
        past_import = False

        for child in node.children:
            text = source_bytes[child.start_byte:child.end_byte].decode()
            if text == "import":
                past_import = True
                continue
            if not past_import:
                continue
            if child.type == "aliased_import":
                orig_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if orig_node:
                    orig = source_bytes[orig_node.start_byte:orig_node.end_byte].decode()
                    if alias_node:
                        alias = source_bytes[alias_node.start_byte:alias_node.end_byte].decode()
                        names.append(alias)
                        aliases[alias] = orig
                    else:
                        names.append(orig)
            elif child.type in ("identifier", "dotted_name"):
                name = source_bytes[child.start_byte:child.end_byte].decode()
                names.append(name)

        return names, aliases

    @staticmethod
    def _walk_flat(node: tree_sitter.Node):
        """Yield only top-level nodes (do not descend into function/class bodies)."""
        for child in node.children:
            yield child
            # Descend into if __name__ == "__main__" blocks only
            if child.type == "if_statement":
                cond = child.child_by_field_name("condition")
                if cond:
                    cond_text = ""
                    try:
                        cond_text = cond.text.decode() if cond.text else ""
                    except Exception:
                        pass
                    if "__name__" in cond_text:
                        body = child.child_by_field_name("consequence")
                        if body:
                            yield from body.children

    def get_call_expressions(
        self, func_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[CallExpression]:
        """Extract call expressions from *func_node*'s body."""
        results: list[CallExpression] = []
        body = func_node.child_by_field_name("body")
        if not body:
            # Module-level — scan directly
            body = func_node
        self._collect_calls(body, source_bytes, results)
        return results

    def _collect_calls(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        results: list[CallExpression],
    ) -> None:
        """Recursively find all call nodes under *node*."""
        for child in node.children:
            if child.type == "call":
                self._process_call(child, source_bytes, results)
            else:
                self._collect_calls(child, source_bytes, results)

    def _process_call(
        self,
        call_node: tree_sitter.Node,
        source_bytes: bytes,
        results: list[CallExpression],
    ) -> None:
        """Parse a single call node into a ``CallExpression``."""
        func_child = call_node.child_by_field_name("function")
        if not func_child:
            return

        line = call_node.start_point[0] + 1

        if func_child.type == "identifier":
            # Simple call: foo(...)
            name = source_bytes[func_child.start_byte:func_child.end_byte].decode()
            results.append(
                CallExpression(
                    callee_name=name,
                    is_self_qualified=False,
                    receiver_name=None,
                    line=line,
                )
            )
        elif func_child.type == "attribute":
            # Method call: obj.method(...) or self.attr.method(...)
            obj_node = func_child.child_by_field_name("object")
            attr_node = func_child.child_by_field_name("attribute")
            if not obj_node or not attr_node:
                return

            attr_name = source_bytes[attr_node.start_byte:attr_node.end_byte].decode()

            # Detect self.X.method() chain (3-level: self.attr.method)
            if obj_node.type == "attribute":
                obj_obj = obj_node.child_by_field_name("object")
                obj_attr = obj_node.child_by_field_name("attribute")
                if obj_obj and obj_attr:
                    obj_obj_text = source_bytes[obj_obj.start_byte:obj_obj.end_byte].decode()
                    receiver = source_bytes[obj_attr.start_byte:obj_attr.end_byte].decode()
                    is_self = obj_obj_text == "self"
                    results.append(
                        CallExpression(
                            callee_name=attr_name,
                            is_self_qualified=is_self,
                            # For self.attr.method() pass receiver; for other.attr.method()
                            # pass the intermediate var name as receiver
                            receiver_name=receiver,
                            line=line,
                        )
                    )
            elif obj_node.type in ("identifier", "call"):
                if obj_node.type == "call":
                    # super().__init__() — obj_node is call(super, ())
                    # emit as receiver_name="super" so the resolver can handle it
                    obj_text = "super"
                else:
                    obj_text = source_bytes[obj_node.start_byte:obj_node.end_byte].decode()
                is_self = obj_text == "self"
                results.append(
                    CallExpression(
                        callee_name=attr_name,
                        is_self_qualified=is_self,
                        # Pass receiver_name for ALL non-self objects (local var tracing)
                        # For self.method() it stays None (no intermediate attr)
                        receiver_name=None if is_self else obj_text,
                        line=line,
                    )
                )

        # Recurse into arguments for nested calls
        args = call_node.child_by_field_name("arguments")
        if args:
            self._collect_calls(args, source_bytes, results)

    def get_inheritance_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Return raw base class name strings from the class argument list.

        Filters out ``ABC``, ``object``, and ``Protocol`` — those are captured
        via ``is_interface_like`` on the resolved entity side, not here.
        Actually — we return ALL bases and let the resolver decide INHERITS vs
        IMPLEMENTS, but we do strip built-ins that are irrelevant.
        """
        bases: list[str] = []
        arg_list = class_node.child_by_field_name("superclasses")
        if not arg_list:
            return bases

        for child in arg_list.children:
            if child.type in ("identifier", "dotted_name"):
                name = source_bytes[child.start_byte:child.end_byte].decode()
                # Strip Python-internal base types that carry no graph meaning
                if name not in ("object",):
                    bases.append(name)
            elif child.type == "attribute":
                # e.g. abc.ABC
                name = source_bytes[child.start_byte:child.end_byte].decode()
                bases.append(name)

        return bases

    def get_implements_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Python has no ``implements`` keyword — returns empty list.

        ABC/Protocol bases are returned by ``get_inheritance_bases`` and
        classified by the resolver using ``is_interface_like``.
        """
        return []

    def is_interface_like(self, entity_type: str, file_path: str) -> bool:
        """True for entities typed as ``"interface"`` (i.e. in interfaces/ dir)."""
        return entity_type == "interface"
