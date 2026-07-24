"""TypeScript language adapter.

Implements ``LanguageAdapter`` for TypeScript (.ts / .tsx) source files.
All TypeScript-specific AST node-type knowledge lives here.

Node-type map reference (tree-sitter-typescript):
  class_declaration        -> "class"
  interface_declaration    -> "interface"
  function_declaration     -> "function"
  method_definition        -> "method"
  method_signature         -> "method"
  abstract_method_signature-> "method"
  export_statement         -> wrapper; unwrap to inner decl
"""

from __future__ import annotations

from typing import Iterator, Optional

import tree_sitter
import tree_sitter_typescript

from src.languages.base import (
    CallExpression,
    EntityDecl,
    ImportStatement,
    LanguageAdapter,
)


class TypeScriptAdapter(LanguageAdapter):
    """Language adapter for TypeScript (.ts / .tsx) files."""

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def language_name(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> list[str]:
        return [".ts", ".tsx"]

    # ------------------------------------------------------------------
    # Parser bootstrap
    # ------------------------------------------------------------------

    def get_tree_sitter_language(self) -> tree_sitter.Language:
        return tree_sitter.Language(tree_sitter_typescript.language_typescript())

    # ------------------------------------------------------------------
    # Entity extraction helpers
    # ------------------------------------------------------------------

    def has_module_docstring(
        self, root_node: tree_sitter.Node, source_bytes: bytes
    ) -> bool:
        for child in root_node.children:
            if child.type == "comment":
                text = source_bytes[child.start_byte:child.end_byte]
                if text.startswith(b"/**"):
                    return True
        return False

    @staticmethod
    def _get_line_range(node: tree_sitter.Node) -> tuple[int, int]:
        start_l = node.start_point[0] + 1
        end_l = node.end_point[0] + 1
        if node.end_point[1] == 0 and end_l > start_l:
            end_l -= 1
        return start_l, end_l

    @staticmethod
    def _has_preceding_jsdoc(node: tree_sitter.Node, source_bytes: bytes) -> bool:
        prev = node.prev_sibling
        while prev:
            if prev.type == "comment":
                text = source_bytes[prev.start_byte:prev.end_byte]
                if text.startswith(b"/**"):
                    return True
            elif prev.type in (";", ","):
                prev = prev.prev_sibling
                continue
            else:
                break
            prev = prev.prev_sibling
        return False

    def iter_child_decls(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        is_inside_class: bool,
        rel_path: str,
    ) -> Iterator[EntityDecl]:
        """Yield top-level declarations from *node*.

        Handles:
        - export_statement wrapping class/interface/function declarations
        - bare class_declaration / interface_declaration / function_declaration
        - method_definition / method_signature / abstract_method_signature (inside class body)
        """
        for child in node.children:
            if child.type == "export_statement":
                # Unwrap: export class Foo / export function bar / export interface I
                for sub in child.children:
                    if sub.type in (
                        "class_declaration",
                        "interface_declaration",
                        "function_declaration",
                    ):
                        yield from self._process_ts_decl(
                            sub, source_bytes, outer_node=child
                        )
            elif child.type in (
                "class_declaration",
                "interface_declaration",
                "function_declaration",
            ):
                yield from self._process_ts_decl(child, source_bytes, outer_node=None)
            elif child.type in (
                "method_definition",
                "method_signature",
                "abstract_method_signature",
            ):
                # Inside a class/interface body
                m_name_node = child.child_by_field_name("name")
                if not m_name_node:
                    continue
                m_name = source_bytes[m_name_node.start_byte:m_name_node.end_byte].decode()
                m_start, m_end = self._get_line_range(child)
                m_doc = self._has_preceding_jsdoc(child, source_bytes)
                m_src = source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

                yield EntityDecl(
                    name=m_name,
                    entity_type="method",
                    start_line=m_start,
                    end_line=m_end,
                    has_docstring=m_doc,
                    source=m_src,
                    body_node=None,
                    is_class_scope=False,
                    ast_node=child,
                )

    def _process_ts_decl(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        outer_node: Optional[tree_sitter.Node],
    ) -> Iterator[EntityDecl]:
        """Yield an EntityDecl for a class/interface/function declaration."""
        start_node = outer_node if outer_node else node
        start_l, end_l = self._get_line_range(start_node)
        has_doc = self._has_preceding_jsdoc(start_node, source_bytes) or self._has_preceding_jsdoc(node, source_bytes)

        name_node = node.child_by_field_name("name")
        if not name_node:
            return

        name = source_bytes[name_node.start_byte:name_node.end_byte].decode()
        src = source_bytes[start_node.start_byte:start_node.end_byte].decode("utf-8", errors="replace")

        if node.type in ("class_declaration", "interface_declaration"):
            ent_type = "interface" if node.type == "interface_declaration" else "class"
            body = node.child_by_field_name("body")

            yield EntityDecl(
                name=name,
                entity_type=ent_type,
                start_line=start_l,
                end_line=end_l,
                has_docstring=has_doc,
                source=src,
                body_node=body,
                is_class_scope=True,
                ast_node=node,
            )

        elif node.type == "function_declaration":
            yield EntityDecl(
                name=name,
                entity_type="function",
                start_line=start_l,
                end_line=end_l,
                has_docstring=has_doc,
                source=src,
                body_node=None,
                is_class_scope=False,
                ast_node=node,
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
        """Parse TypeScript import statements.

        Handles:
          import { X } from './path'
          import { X as Y } from './path'
          import DefaultExport from './path'
        """
        results: list[ImportStatement] = []

        for child in root_node.children:
            if child.type != "import_statement":
                continue

            line = child.start_point[0] + 1
            module_path = ""
            imported_names: list[str] = []
            aliases: dict[str, str] = {}

            for sub in child.children:
                if sub.type == "string":
                    # The module path string: './path' or '../interfaces/repo'
                    raw = source_bytes[sub.start_byte:sub.end_byte].decode()
                    module_path = raw.strip("'\"")
                elif sub.type == "import_clause":
                    # { X, Y as Z } or default import
                    for item in sub.children:
                        if item.type == "named_imports":
                            for ni in item.children:
                                if ni.type == "import_specifier":
                                    orig_node = ni.child_by_field_name("name")
                                    alias_node = ni.child_by_field_name("alias")
                                    if orig_node:
                                        orig = source_bytes[orig_node.start_byte:orig_node.end_byte].decode()
                                        if alias_node:
                                            alias = source_bytes[alias_node.start_byte:alias_node.end_byte].decode()
                                            imported_names.append(alias)
                                            aliases[alias] = orig
                                        else:
                                            imported_names.append(orig)
                        elif item.type == "identifier":
                            # default import
                            name = source_bytes[item.start_byte:item.end_byte].decode()
                            imported_names.append(name)

            if module_path:
                is_relative = module_path.startswith("./") or module_path.startswith("../")
                results.append(
                    ImportStatement(
                        module_path=module_path,
                        imported_names=imported_names,
                        aliases=aliases,
                        is_relative=is_relative,
                        line=line,
                    )
                )

        return results

    def get_call_expressions(
        self, func_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[CallExpression]:
        """Extract call expressions from a function/method body."""
        results: list[CallExpression] = []
        body = func_node.child_by_field_name("body")
        if not body:
            body = func_node
        self._collect_calls(body, source_bytes, results)
        return results

    def _collect_calls(
        self,
        node: tree_sitter.Node,
        source_bytes: bytes,
        results: list[CallExpression],
    ) -> None:
        for child in node.children:
            if child.type == "call_expression":
                self._process_call(child, source_bytes, results)
            elif child.type == "new_expression":
                self._process_new_expression(child, source_bytes, results)
            self._collect_calls(child, source_bytes, results)

    def _process_new_expression(
        self,
        new_node: tree_sitter.Node,
        source_bytes: bytes,
        results: list[CallExpression],
    ) -> None:
        line = new_node.start_point[0] + 1
        class_name = None
        for child in new_node.children:
            if child.type in ("identifier", "type_identifier", "member_expression"):
                class_name = source_bytes[child.start_byte:child.end_byte].decode()
                break
        if class_name:
            results.append(
                CallExpression(
                    callee_name="constructor",
                    is_self_qualified=False,
                    receiver_name=class_name,
                    line=line,
                )
            )

    def _process_call(
        self,
        call_node: tree_sitter.Node,
        source_bytes: bytes,
        results: list[CallExpression],
    ) -> None:
        func_child = call_node.child_by_field_name("function")
        if not func_child:
            return

        line = call_node.start_point[0] + 1

        if func_child.type == "identifier":
            name = source_bytes[func_child.start_byte:func_child.end_byte].decode()
            results.append(
                CallExpression(
                    callee_name=name,
                    is_self_qualified=False,
                    receiver_name=None,
                    line=line,
                )
            )
        elif func_child.type == "member_expression":
            # e.g. this.memoryStorage.get(...) or userService.save(...)
            obj_node = func_child.child_by_field_name("object")
            prop_node = func_child.child_by_field_name("property")
            if not obj_node or not prop_node:
                return

            prop_name = source_bytes[prop_node.start_byte:prop_node.end_byte].decode()

            if obj_node.type == "member_expression":
                # this.service.method() or obj.prop.method()
                obj_obj = obj_node.child_by_field_name("object")
                obj_prop = obj_node.child_by_field_name("property")
                if obj_obj and obj_prop:
                    obj_text = source_bytes[obj_obj.start_byte:obj_obj.end_byte].decode()
                    receiver = source_bytes[obj_prop.start_byte:obj_prop.end_byte].decode()
                    is_this = obj_text == "this"
                    results.append(
                        CallExpression(
                            callee_name=prop_name,
                            is_self_qualified=is_this,
                            receiver_name=receiver,
                            line=line,
                        )
                    )
            elif obj_node.type in ("identifier", "this"):
                obj_text = source_bytes[obj_node.start_byte:obj_node.end_byte].decode()
                is_this = obj_text == "this"
                results.append(
                    CallExpression(
                        callee_name=prop_name,
                        is_self_qualified=is_this,
                        receiver_name=None if is_this else obj_text,
                        line=line,
                    )
                )

    def get_inheritance_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Return names from the ``extends`` clause of a class declaration."""
        bases: list[str] = []
        for child in class_node.children:
            if child.type == "class_heritage":
                for item in child.children:
                    if item.type == "extends_clause":
                        for sub in item.children:
                            if sub.type in ("identifier", "type_identifier"):
                                name = source_bytes[sub.start_byte:sub.end_byte].decode()
                                bases.append(name)
        return bases

    def get_implements_bases(
        self, class_node: tree_sitter.Node, source_bytes: bytes
    ) -> list[str]:
        """Return names from the ``implements`` clause of a class declaration."""
        bases: list[str] = []
        for child in class_node.children:
            if child.type == "class_heritage":
                for item in child.children:
                    if item.type == "implements_clause":
                        for sub in item.children:
                            if sub.type in ("identifier", "type_identifier", "generic_type"):
                                if sub.type == "generic_type":
                                    # Repository<UserModel> -> "Repository"
                                    name_node = sub.child_by_field_name("name")
                                    if name_node:
                                        name = source_bytes[name_node.start_byte:name_node.end_byte].decode()
                                        bases.append(name)
                                else:
                                    name = source_bytes[sub.start_byte:sub.end_byte].decode()
                                    bases.append(name)
        return bases

    def is_interface_like(self, entity_type: str, file_path: str) -> bool:
        """True for TypeScript ``interface`` declarations."""
        return entity_type == "interface"
