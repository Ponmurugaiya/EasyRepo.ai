"""Call resolver — language-agnostic.

Resolves ``CallExpression`` objects to CALLS relationship edges.

RESOLUTION STRATEGY (in priority order)
=========================================
1. ``self.attr.method()`` / ``this.attr.method()``
   → Look up attr type via __init__ parameter annotations + self.X = param assignments.
   → Find method on the resolved class.

2. ``self.method()`` / ``this.method()``
   → Search sibling methods on the enclosing class.

3. Local variable type tracing (function body assignments)
   → Scan __init__ and the caller's own source for ``var = ClassName(...)`` or
     ``var = ClassName(key=...)`` patterns.
   → Build a local variable → class entity map.
   → Resolve ``var.method()`` via that map.

4. ``super().__init__()`` / ``super().method()``
   → Walk up to the first superclass found in the symbol table / inheritance chain.

5. Plain ``name(...)`` or ``ClassName(...)``
   → Symbol table lookup.
   → If target is a class: resolve to its __init__ / constructor method.

6. Unresolvable → debug-logged, silently skipped.

This file contains ZERO language-specific logic.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import tree_sitter

from src.extraction.models import Entity, Relationship
from src.languages.base import LanguageAdapter, CallExpression
from src.resolution.import_resolver import SymbolTable, FileSymbolTables

logger = logging.getLogger(__name__)


def resolve_calls(
    entities: list[Entity],
    symbol_tables: FileSymbolTables,
    repo_dir: Path,
    adapter_registry: dict[str, LanguageAdapter],
) -> list[Relationship]:
    """Resolve all call expressions to CALLS relationship edges."""
    relationships: list[Relationship] = []
    seen: set[tuple[str, str]] = set()

    entity_by_id: dict[str, Entity] = {e.id: e for e in entities}

    children_of: dict[str, list[Entity]] = {}
    for e in entities:
        if e.parent_id:
            children_of.setdefault(e.parent_id, []).append(e)

    name_in_file: dict[str, dict[str, Entity]] = {}
    for e in entities:
        name_in_file.setdefault(e.file_path, {})[e.name] = e

    # Pre-parse ASTs once per file (avoid re-parsing for every entity)
    file_trees: dict[str, tree_sitter.Tree] = {}
    file_bytes: dict[str, bytes] = {}
    file_adapters: dict[str, LanguageAdapter] = {}

    for caller in entities:
        if caller.type not in ("function", "method", "module"):
            continue

        suffix = Path(caller.file_path).suffix
        adapter = adapter_registry.get(suffix)
        if adapter is None:
            continue

        # Parse file once per file_path
        if caller.file_path not in file_trees:
            full_path = repo_dir / caller.file_path
            if not full_path.exists():
                continue
            try:
                src_bytes = full_path.read_bytes()
            except OSError:
                continue
            import tree_sitter as _ts
            lang = adapter.get_tree_sitter_language()
            parser = _ts.Parser(lang)
            tree = parser.parse(src_bytes)
            if not tree:
                continue
            file_trees[caller.file_path] = tree
            file_bytes[caller.file_path] = src_bytes
            file_adapters[caller.file_path] = adapter

        tree = file_trees.get(caller.file_path)
        source_bytes = file_bytes.get(caller.file_path)
        if not tree or source_bytes is None:
            continue

        # Find the AST node for this entity
        func_node = _find_node_by_lines(
            tree.root_node, caller.start_line, caller.end_line
        )
        if func_node is None:
            if caller.type == "module":
                func_node = tree.root_node
            else:
                continue

        # For MODULE entities: only scan top-level code, NOT function/class bodies.
        # The adapter's get_call_expressions descends into everything; for module entities
        # we use the root node but should only get top-level calls (e.g. main().catch(...)).
        # To avoid false attribution, we let the adapter handle it naturally, but we SKIP
        # constructor calls that resolve to classes (those belong to the function that
        # contains them, not the module).
        call_exprs = adapter.get_call_expressions(func_node, source_bytes)
        if caller.type == "module":
            # Keep only direct module-level calls (not inside nested function/class scopes)
            # Filter: calls within line ranges of known child entities are excluded
            child_line_ranges = [
                (e.start_line, e.end_line)
                for e in children_of.get(caller.id, [])
            ]
            call_exprs = [
                c for c in call_exprs
                if not any(s <= c.line <= e for s, e in child_line_ranges)
            ]

        file_symbols = symbol_tables.get(caller.file_path, {})

        parent_entity: Optional[Entity] = None
        if caller.parent_id:
            parent_entity = entity_by_id.get(caller.parent_id)

        # Build self-attr type map from __init__ annotations + assignments
        self_attr_types = _build_self_attr_types(
            parent_entity, children_of, entity_by_id, file_symbols, name_in_file.get(caller.file_path, {})
        )

        # Build local variable type map from this function's source
        caller_source = caller.source or ""
        local_var_types = _build_local_var_types(
            caller_source, file_symbols, name_in_file.get(caller.file_path, {}), entity_by_id
        )

        # Also trace function-level instantiations for module-scope
        if caller.type == "module":
            local_var_types.update(
                _build_local_var_types(
                    source_bytes.decode("utf-8", errors="replace"),
                    file_symbols, name_in_file.get(caller.file_path, {}), entity_by_id
                )
            )

        for call in call_exprs:
            target_id = _resolve_call(
                call=call,
                caller=caller,
                parent_entity=parent_entity,
                self_attr_types=self_attr_types,
                local_var_types=local_var_types,
                file_symbols=file_symbols,
                entity_by_id=entity_by_id,
                children_of=children_of,
                name_in_file=name_in_file.get(caller.file_path, {}),
                entities=entities,
            )

            if target_id and not target_id.startswith("external:"):
                if target_id in entity_by_id:
                    edge = (caller.id, target_id)
                    if edge not in seen:
                        seen.add(edge)
                        relationships.append(
                            Relationship(
                                source_id=caller.id,
                                target_id=target_id,
                                type="CALLS",
                                file_path=caller.file_path,
                                line=call.line,
                            )
                        )

    return relationships


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def _resolve_call(
    call: CallExpression,
    caller: Entity,
    parent_entity: Optional[Entity],
    self_attr_types: dict[str, str],
    local_var_types: dict[str, str],
    file_symbols: SymbolTable,
    entity_by_id: dict[str, Entity],
    children_of: dict[str, list[Entity]],
    name_in_file: dict[str, Entity],
    entities: list[Entity],
) -> Optional[str]:

    # -----------------------------------------------------------------------
    # 1. self.attr.method() or this.attr.method()
    # -----------------------------------------------------------------------
    if call.is_self_qualified and call.receiver_name:
        class_id = self_attr_types.get(call.receiver_name)
        if class_id:
            found = _find_method(class_id, call.callee_name, children_of)
            if found:
                return found

    # -----------------------------------------------------------------------
    # 2. self.method() / this.method() — sibling
    # -----------------------------------------------------------------------
    if call.is_self_qualified and call.receiver_name is None:
        if parent_entity:
            found = _find_method(parent_entity.id, call.callee_name, children_of)
            if found:
                return found

    # -----------------------------------------------------------------------
    # 3. Local variable: var.method()
    # -----------------------------------------------------------------------
    if call.receiver_name and not call.is_self_qualified:
        class_id = local_var_types.get(call.receiver_name)
        if class_id:
            found = _find_method(class_id, call.callee_name, children_of)
            if found:
                return found
        else:
            # Fallback for untyped local variables (e.g. retrievedUser.toJSON()):
            # check if callee_name is a unique method across all extracted entities
            unique_target = _find_unique_method_across_entities(call.callee_name, entities)
            if unique_target:
                return unique_target

    # -----------------------------------------------------------------------
    # 4. super() calls: super().__init__() or super().to_dict()
    # -----------------------------------------------------------------------
    if call.receiver_name == "super" or (call.callee_name in ("__init__", "constructor") and not call.is_self_qualified and not call.receiver_name):
        if parent_entity:
            parent_class_id = _get_superclass_id(
                parent_entity, file_symbols, name_in_file, entity_by_id
            )
            if parent_class_id:
                found = _find_method(parent_class_id, call.callee_name, children_of)
                if found:
                    return found

    # -----------------------------------------------------------------------
    # 5. Constructor call: new ClassName(...) or ClassName(...)
    #    adapter emits callee_name="constructor", receiver_name=ClassName
    # -----------------------------------------------------------------------
    if call.callee_name == "constructor" and call.receiver_name:
        class_name = call.receiver_name
        class_id = _lookup_name(class_name, file_symbols, name_in_file)
        if class_id:
            ctor = _find_method(class_id, "constructor", children_of) or \
                   _find_method(class_id, "__init__", children_of)
            if ctor:
                return ctor
            return None

    # -----------------------------------------------------------------------
    # 6. Plain function/class call: name(...) [no receiver, no self]
    # -----------------------------------------------------------------------
    if not call.receiver_name and not call.is_self_qualified:
        target_id = _lookup_name(call.callee_name, file_symbols, name_in_file)
        if target_id:
            target_ent = entity_by_id.get(target_id)
            if target_ent and target_ent.type in ("class", "interface"):
                ctor = _find_method(target_id, "__init__", children_of) or \
                       _find_method(target_id, "constructor", children_of)
                return ctor or target_id
            return target_id

    return None


# ---------------------------------------------------------------------------
# Type tracing helpers
# ---------------------------------------------------------------------------

def _build_self_attr_types(
    class_entity: Optional[Entity],
    children_of: dict[str, list[Entity]],
    entity_by_id: dict[str, Entity],
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
) -> dict[str, str]:
    """Map self.attr_name → class_entity_id by scanning __init__ annotations and assignments."""
    result: dict[str, str] = {}
    if not class_entity:
        return result

    init_method: Optional[Entity] = None
    for child in children_of.get(class_entity.id, []):
        if child.name in ("__init__", "constructor") and child.type == "method":
            init_method = child
            break
    if not init_method:
        return result

    source = init_method.source or ""

    # 1. Parameter type annotations: (param_name: TypeName)
    param_types: dict[str, str] = {}
    sig_match = re.search(r"(?:def\s+__init__|constructor)\s*\((.+?)\)\s*(?:->.*?)?[:{]", source, re.DOTALL)
    if sig_match:
        sig = sig_match.group(1)
        for m in re.finditer(r"(\w+)\s*:\s*([A-Z]\w*)", sig):
            param_name, type_name = m.group(1), m.group(2)
            if param_name in ("self", "cls"):
                continue
            type_id = _lookup_name(type_name, file_symbols, name_in_file)
            if type_id and not type_id.startswith("external:"):
                param_types[param_name] = type_id

    # TypeScript constructor: private/public/readonly param name: TypeName
    for m in re.finditer(r"(?:private|public|protected|readonly)\s+(\w+)\s*:\s*([A-Z]\w*)", source):
        param_name, type_name = m.group(1), m.group(2)
        type_id = _lookup_name(type_name, file_symbols, name_in_file)
        if type_id and not type_id.startswith("external:"):
            param_types[param_name] = type_id

    # 2. self.X = param_name  assignments
    for param_name, type_id in param_types.items():
        result[param_name] = type_id
        for m in re.finditer(rf"(?:self|this)\.(\w+)\s*=\s*{param_name}\b", source):
            attr = m.group(1)
            result[attr] = type_id

    return result


def _build_local_var_types(
    source: str,
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
    entity_by_id: dict[str, Entity],
) -> dict[str, str]:
    """Scan function source for ``var = ClassName(...)`` patterns.

    Returns mapping of local_variable_name → class_entity_id.
    """
    result: dict[str, str] = {}

    # Pattern: var_name = ClassName(args)  [may have keyword args]
    # Also handles: var_name = new ClassName(args)  (TS)
    patterns = [
        # Python: var = ClassName(...)  or  var = ClassName(key=val, ...)
        r"(\w+)\s*=\s*([A-Z]\w*)\s*\(",
        # TypeScript: const var = new ClassName(...)
        r"(?:const|let|var)\s+(\w+)\s*=\s*new\s+([A-Z]\w*)\s*\(",
        # Python: var = SomeAlias(...)
        r"(\w+)\s*=\s*([A-Z]\w+)\(",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, source):
            var_name = m.group(1)
            class_name = m.group(2)
            class_id = _lookup_name(class_name, file_symbols, name_in_file)
            if class_id and not class_id.startswith("external:"):
                ent = entity_by_id.get(class_id)
                if ent and ent.type in ("class", "interface"):
                    result[var_name] = class_id

    return result


def _get_superclass_id(
    entity: Entity,
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
    entity_by_id: dict[str, Entity],
) -> Optional[str]:
    """Find the first superclass of *entity* by scanning its source for base class names."""
    if not entity.source:
        return None

    # Python: class Foo(BaseClass):  or class Foo(BaseClass, Mixin):
    m = re.search(r"class\s+\w+\s*\(([^)]+)\)", entity.source)
    if not m:
        # TypeScript: class Foo extends Bar
        m = re.search(r"class\s+\w+\s+extends\s+(\w+)", entity.source)
        if m:
            base_name = m.group(1).strip()
            return _lookup_name(base_name, file_symbols, name_in_file)
        return None

    bases_raw = m.group(1)
    for base in bases_raw.split(","):
        base_name = base.strip().split(".")[0]  # handle abc.ABC -> abc
        if base_name in ("ABC", "object", "Protocol"):
            continue
        resolved = _lookup_name(base_name, file_symbols, name_in_file)
        if resolved:
            return resolved

    return None


def _find_method(
    class_id: str,
    method_name: str,
    children_of: dict[str, list[Entity]],
) -> Optional[str]:
    for child in children_of.get(class_id, []):
        if child.name == method_name and child.type == "method":
            return child.id
    return None


def _lookup_name(
    name: str,
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
) -> Optional[str]:
    sym = file_symbols.get(name)
    if sym and not sym.startswith("external:"):
        return sym
    local = name_in_file.get(name)
    if local:
        return local.id
    return None


def _find_node_by_lines(
    root: tree_sitter.Node, start_line: int, end_line: int
) -> Optional[tree_sitter.Node]:
    best: Optional[tree_sitter.Node] = None

    def visit(node: tree_sitter.Node) -> None:
        nonlocal best
        node_start = node.start_point[0] + 1
        node_end = node.end_point[0] + 1
        if node_start <= start_line and node_end >= end_line:
            if node.type in (
                "function_definition",
                "class_definition",
                "method_definition",
                "function_declaration",
                "class_declaration",
                "decorated_definition",
                "interface_declaration",
                "method_signature",
                "abstract_method_signature",
            ):
                cur_span = node_end - node_start
                best_span = (
                    (best.end_point[0] - best.start_point[0]) if best else float("inf")
                )
                if cur_span <= best_span:
                    best = node
        for child in node.children:
            visit(child)

    visit(root)
    return best


def _find_unique_method_across_entities(
    method_name: str, entities: list[Entity]
) -> Optional[str]:
    """Return entity ID if method_name is unique among all method entities, else None."""
    if method_name in ("constructor", "__init__"):
        return None
    matches = [
        e.id for e in entities
        if e.type == "method" and e.name == method_name
    ]
    if len(matches) == 1:
        return matches[0]
    return None
