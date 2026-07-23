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
"""

import os
from pathlib import Path
from typing import List, Tuple, Optional, Set, Dict

import tree_sitter
from src.extraction.models import Entity, Relationship
from src.extraction.parser import TreeSitterParser


class EntityExtractor:
    """Extracts code entities and CONTAINS relationships from source repositories."""

    def __init__(self) -> None:
        self.parser = TreeSitterParser()

    def generate_module_id(self, relative_path: str, language: str) -> str:
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

    def extract_repository(self, repo_path: str) -> Tuple[List[Entity], List[Relationship]]:
        repo_dir = Path(repo_path).resolve()
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__", "platform")]
            for file in sorted(files):
                full_path = Path(root) / file
                rel_path = full_path.relative_to(repo_dir).as_posix()

                if file.endswith(".py"):
                    language = "python"
                elif file.endswith(".ts") or file.endswith(".tsx"):
                    language = "typescript"
                elif file.endswith(".md"):
                    language = "markdown"
                else:
                    continue

                file_ents, file_rels = self.extract_file(str(full_path), rel_path, language)
                entities.extend(file_ents)
                relationships.extend(file_rels)

        return entities, relationships

    def extract_file(self, full_path: str, rel_path: str, language: str) -> Tuple[List[Entity], List[Relationship]]:
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

        if language == "markdown":
            return entities, relationships

        source_bytes = source_text.encode("utf-8")
        tree = self.parser.parse(source_bytes, language)
        if not tree:
            return entities, relationships

        root_node = tree.root_node

        if language == "python":
            file_ents, file_rels, has_mod_doc = self._extract_python(root_node, source_bytes, module_entity, rel_path)
            module_entity.has_docstring = has_mod_doc
        elif language == "typescript":
            file_ents, file_rels, has_mod_doc = self._extract_typescript(root_node, source_bytes, module_entity, rel_path)
            module_entity.has_docstring = has_mod_doc

        entities.extend(file_ents)
        relationships.extend(file_rels)

        for ent in file_ents:
            relationships.append(
                Relationship(
                    source_id=ent.parent_id,
                    target_id=ent.id,
                    type="CONTAINS",
                    file_path=ent.file_path,
                    line=ent.start_line,
                )
            )

        return entities, relationships

    def _extract_python(
        self, root_node: tree_sitter.Node, source_bytes: bytes, module_entity: Entity, rel_path: str
    ) -> Tuple[List[Entity], List[Relationship], bool]:
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        has_module_doc = self._python_has_docstring(root_node)

        is_interface_file = "interfaces" in rel_path

        def get_line_range(node: tree_sitter.Node) -> Tuple[int, int]:
            start_l = node.start_point[0] + 1
            end_l = node.end_point[0] + 1
            if node.end_point[1] == 0 and end_l > start_l:
                end_l -= 1
            return start_l, end_l

        def walk(node: tree_sitter.Node, current_parent_id: str, is_inside_class: bool):
            for child in node.children:
                actual_node = child
                if child.type == "decorated_definition":
                    actual_node = child

                target = actual_node
                if target.type == "decorated_definition":
                    inner_func = target.child_by_field_name("definition")
                    if inner_func and inner_func.type == "function_definition":
                        target_func = inner_func
                    else:
                        target_func = None
                        for sub in target.children:
                            if sub.type in ("function_definition", "class_definition"):
                                target_func = sub
                                break
                    if not target_func:
                        continue
                    
                    name_node = target_func.child_by_field_name("name")
                    if not name_node:
                        continue
                    ent_name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                    start_l, end_l = get_line_range(target)
                    
                    if target_func.type == "class_definition":
                        ent_type = "interface" if is_interface_file else "class"
                    else:
                        ent_type = "method" if is_inside_class else "function"

                    ent_id = f"{current_parent_id}.{ent_name}"
                    has_doc = self._python_has_docstring(target_func)
                    ent_src = source_bytes[target.start_byte : target.end_byte].decode("utf-8")

                    ent = Entity(
                        id=ent_id,
                        type=ent_type,
                        name=ent_name,
                        file_path=rel_path,
                        start_line=start_l,
                        end_line=end_l,
                        parent_id=current_parent_id,
                        language="python",
                        has_docstring=has_doc,
                        source=ent_src,
                    )
                    entities.append(ent)

                    body = target_func.child_by_field_name("body")
                    if body:
                        walk(body, ent_id, is_inside_class=(ent_type in ("class", "interface")))

                elif target.type == "class_definition":
                    name_node = target.child_by_field_name("name")
                    if name_node:
                        ent_name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                        start_l, end_l = get_line_range(target)
                        ent_type = "interface" if is_interface_file else "class"
                        ent_id = f"{current_parent_id}.{ent_name}"
                        has_doc = self._python_has_docstring(target)
                        ent_src = source_bytes[target.start_byte : target.end_byte].decode("utf-8")

                        ent = Entity(
                            id=ent_id,
                            type=ent_type,
                            name=ent_name,
                            file_path=rel_path,
                            start_line=start_l,
                            end_line=end_l,
                            parent_id=current_parent_id,
                            language="python",
                            has_docstring=has_doc,
                            source=ent_src,
                        )
                        entities.append(ent)

                        body = target.child_by_field_name("body")
                        if body:
                            walk(body, ent_id, is_inside_class=True)

                elif target.type == "function_definition":
                    name_node = target.child_by_field_name("name")
                    if name_node:
                        ent_name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                        start_l, end_l = get_line_range(target)
                        ent_type = "method" if is_inside_class else "function"
                        ent_id = f"{current_parent_id}.{ent_name}"
                        has_doc = self._python_has_docstring(target)
                        ent_src = source_bytes[target.start_byte : target.end_byte].decode("utf-8")

                        ent = Entity(
                            id=ent_id,
                            type=ent_type,
                            name=ent_name,
                            file_path=rel_path,
                            start_line=start_l,
                            end_line=end_l,
                            parent_id=current_parent_id,
                            language="python",
                            has_docstring=has_doc,
                            source=ent_src,
                        )
                        entities.append(ent)

                        body = target.child_by_field_name("body")
                        if body:
                            walk(body, ent_id, is_inside_class=is_inside_class)
                else:
                    if target.type not in ("class_definition", "function_definition", "decorated_definition"):
                        walk(target, current_parent_id, is_inside_class)

        walk(root_node, module_entity.id, is_inside_class=False)
        return entities, relationships, has_module_doc

    def _python_has_docstring(self, node: tree_sitter.Node) -> bool:
        body = node.child_by_field_name("body") if node.type in ("function_definition", "class_definition") else node
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
            elif child.type in ("import_statement", "import_from_statement", "pass_statement"):
                continue
            else:
                break
        return False

    def _extract_typescript(
        self, root_node: tree_sitter.Node, source_bytes: bytes, module_entity: Entity, rel_path: str
    ) -> Tuple[List[Entity], List[Relationship], bool]:
        entities: List[Entity] = []
        relationships: List[Relationship] = []

        has_module_doc = False
        for child in root_node.children:
            if child.type == "comment" and source_bytes[child.start_byte : child.end_byte].startswith(b"/**"):
                has_module_doc = True
                break

        def get_line_range(node: tree_sitter.Node) -> Tuple[int, int]:
            start_l = node.start_point[0] + 1
            end_l = node.end_point[0] + 1
            if node.end_point[1] == 0 and end_l > start_l:
                end_l -= 1
            return start_l, end_l

        def has_preceding_jsdoc(node: tree_sitter.Node) -> bool:
            prev = node.prev_sibling
            while prev:
                if prev.type == "comment":
                    text = source_bytes[prev.start_byte : prev.end_byte]
                    if text.startswith(b"/**"):
                        return True
                elif prev.type in (";", ","):
                    prev = prev.prev_sibling
                    continue
                else:
                    break
                prev = prev.prev_sibling
            return False

        def process_decl(node: tree_sitter.Node, current_parent_id: str, outer_node: Optional[tree_sitter.Node] = None):
            start_node = outer_node if outer_node else node
            start_l, end_l = get_line_range(start_node)
            has_doc = has_preceding_jsdoc(start_node) or has_preceding_jsdoc(node)

            if node.type in ("class_declaration", "interface_declaration"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    ent_name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                    ent_type = "interface" if node.type == "interface_declaration" else "class"
                    ent_id = f"{current_parent_id}.{ent_name}"
                    ent_src = source_bytes[start_node.start_byte : start_node.end_byte].decode("utf-8")

                    ent = Entity(
                        id=ent_id,
                        type=ent_type,
                        name=ent_name,
                        file_path=rel_path,
                        start_line=start_l,
                        end_line=end_l,
                        parent_id=current_parent_id,
                        language="typescript",
                        has_docstring=has_doc,
                        source=ent_src,
                    )
                    entities.append(ent)

                    body = node.child_by_field_name("body")
                    if body:
                        for item in body.children:
                            if item.type in ("method_definition", "method_signature", "abstract_method_signature"):
                                m_name_node = item.child_by_field_name("name")
                                if m_name_node:
                                    m_name = source_bytes[m_name_node.start_byte : m_name_node.end_byte].decode("utf-8")
                                    m_start, m_end = get_line_range(item)
                                    m_doc = has_preceding_jsdoc(item)
                                    m_src = source_bytes[item.start_byte : item.end_byte].decode("utf-8")
                                    m_id = f"{ent_id}.{m_name}"

                                    m_ent = Entity(
                                        id=m_id,
                                        type="method",
                                        name=m_name,
                                        file_path=rel_path,
                                        start_line=m_start,
                                        end_line=m_end,
                                        parent_id=ent_id,
                                        language="typescript",
                                        has_docstring=m_doc,
                                        source=m_src,
                                    )
                                    entities.append(m_ent)

            elif node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    ent_name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf-8")
                    ent_id = f"{current_parent_id}.{ent_name}"
                    ent_src = source_bytes[start_node.start_byte : start_node.end_byte].decode("utf-8")

                    ent = Entity(
                        id=ent_id,
                        type="function",
                        name=ent_name,
                        file_path=rel_path,
                        start_line=start_l,
                        end_line=end_l,
                        parent_id=current_parent_id,
                        language="typescript",
                        has_docstring=has_doc,
                        source=ent_src,
                    )
                    entities.append(ent)

        for child in root_node.children:
            if child.type == "export_statement":
                for sub in child.children:
                    if sub.type in ("class_declaration", "interface_declaration", "function_declaration"):
                        process_decl(sub, module_entity.id, outer_node=child)
            elif child.type in ("class_declaration", "interface_declaration", "function_declaration"):
                process_decl(child, module_entity.id)

        return entities, relationships, has_module_doc
