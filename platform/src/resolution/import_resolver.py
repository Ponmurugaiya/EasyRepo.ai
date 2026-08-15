"""Import resolver — language-agnostic.

Consumes ``ImportStatement`` objects from adapter.get_imports() and builds:
  1. Per-file symbol tables  (local_name -> entity_id or "external:<path>")
  2. IMPORTS relationships   (source_module entity -> target entity)

This file contains ZERO language-specific logic.  It operates on
normalised ``ImportStatement`` objects produced by the adapters.

ROOT CAUSE FIXES vs. initial implementation:
  - Python relative import `.base` in `python/models/user.py`: `current_dir`
    is `python/models`, dots=1 means stay in that dir → target = `python/models/base`
    → entity ID `py.models.base`.  The old code navigated *dots-1* levels up,
    which for dots=1 meant zero levels (correct), but PurePosixPath("python/models")
    has parts ["python","models"] so the join worked.  The real bug was in
    `_python_parts_to_id` which was not stripping the "python" prefix when
    the parts were built from the filesystem path (not from the module dotted-name).
  - TypeScript path normalisation: `PurePosixPath` does not collapse `..` segments;
    we now use `Path(...).resolve()` relative to a virtual root.
"""

from __future__ import annotations

import logging
import os
import posixpath
from pathlib import Path, PurePosixPath
from typing import Optional

import tree_sitter

from src.extraction.models import Entity, Relationship
from src.languages.base import LanguageAdapter

logger = logging.getLogger(__name__)

# Per-file symbol table: local_name -> entity_id (or "external:<module>")
SymbolTable = dict[str, str]
# file_path -> SymbolTable
FileSymbolTables = dict[str, SymbolTable]


def build_symbol_tables(
    entities: list[Entity],
    repo_dir: Path,
    adapter_registry: dict[str, LanguageAdapter],
) -> tuple[FileSymbolTables, list[Relationship]]:
    """Parse imports for every source file, build symbol tables, emit IMPORTS edges."""
    entity_by_id: dict[str, Entity] = {e.id: e for e in entities}

    module_entities: dict[str, Entity] = {
        e.id: e for e in entities if e.type == "module"
    }
    # (file_path, name) -> entity  for named child lookup
    name_in_file: dict[tuple[str, str], Entity] = {}
    for e in entities:
        if e.type != "module":
            name_in_file[(e.file_path, e.name)] = e

    # Build a file_path -> module_entity lookup for fast resolution
    file_path_to_module: dict[str, Entity] = {
        e.file_path: e for e in module_entities.values()
    }

    symbol_tables: FileSymbolTables = {}
    import_rels: list[Relationship] = []

    for mod_entity in module_entities.values():
        suffix = Path(mod_entity.file_path).suffix
        adapter = adapter_registry.get(suffix)
        if adapter is None:
            continue

        full_path = repo_dir / mod_entity.file_path
        if not full_path.exists():
            continue

        try:
            source_bytes = full_path.read_bytes()
        except OSError:
            logger.debug("Cannot read %s", full_path)
            continue

        import tree_sitter as _ts
        lang = adapter.get_tree_sitter_language()
        parser = _ts.Parser(lang)
        tree = parser.parse(source_bytes)
        if not tree:
            continue

        imports = adapter.get_imports(tree.root_node, source_bytes, mod_entity.file_path)
        file_symbols: SymbolTable = {}

        for imp in imports:
            resolved_module_id = _resolve_module_path(
                imp.module_path,
                imp.is_relative,
                mod_entity.file_path,
                adapter.language_name,
                module_entities,
                file_path_to_module,
            )

            for local_name in imp.imported_names:
                original_name = imp.aliases.get(local_name, local_name)

                if resolved_module_id:
                    resolved_mod = module_entities.get(resolved_module_id)
                    if resolved_mod:
                        candidate_key = (resolved_mod.file_path, original_name)
                        target_ent = name_in_file.get(candidate_key)
                        if target_ent:
                            file_symbols[local_name] = target_ent.id
                            import_rels.append(
                                Relationship(
                                    source_id=mod_entity.id,
                                    target_id=target_ent.id,
                                    type="IMPORTS",
                                    file_path=mod_entity.file_path,
                                    line=imp.line,
                                )
                            )
                        else:
                            # Name not found as a child entity — import the module
                            file_symbols[local_name] = resolved_module_id
                            import_rels.append(
                                Relationship(
                                    source_id=mod_entity.id,
                                    target_id=resolved_module_id,
                                    type="IMPORTS",
                                    file_path=mod_entity.file_path,
                                    line=imp.line,
                                )
                            )
                    else:
                        file_symbols[local_name] = f"external:{imp.module_path}"
                else:
                    file_symbols[local_name] = f"external:{imp.module_path}"
                    logger.debug(
                        "Unresolvable import: %s from %s in %s",
                        local_name, imp.module_path, mod_entity.file_path,
                    )

            # Bare ``import module`` or ``import module as X``
            if not imp.imported_names:
                if imp.aliases:
                    local_module_name = list(imp.aliases.keys())[0]
                else:
                    local_module_name = imp.module_path.split(".")[-1]
                if resolved_module_id:
                    file_symbols[local_module_name] = resolved_module_id
                else:
                    file_symbols[local_module_name] = f"external:{imp.module_path}"

        symbol_tables[mod_entity.file_path] = file_symbols

    return symbol_tables, import_rels


# ---------------------------------------------------------------------------
# Module path resolution helpers (language-agnostic)
# ---------------------------------------------------------------------------

def _resolve_module_path(
    module_path: str,
    is_relative: bool,
    current_file_path: str,
    language_name: str,
    module_entities: dict[str, Entity],
    file_path_to_module: dict[str, Entity],
) -> Optional[str]:
    if is_relative:
        return _resolve_relative(
            module_path, current_file_path, language_name, module_entities, file_path_to_module
        )
    else:
        return _resolve_absolute(module_path, language_name, module_entities)


def _resolve_relative(
    module_path: str,
    current_file_path: str,
    language_name: str,
    module_entities: dict[str, Entity],
    file_path_to_module: dict[str, Entity],
) -> Optional[str]:
    current_dir = PurePosixPath(current_file_path).parent

    if language_name == "python":
        # Count leading dots
        dots = len(module_path) - len(module_path.lstrip("."))
        rest = module_path.lstrip(".")

        # Use the same virtual-root technique as TypeScript to avoid
        # PurePosixPath(".").as_posix() == "." producing "./foo" paths
        # that never match any key in file_path_to_module.
        # e.g. server/app.py + "..models" → dots=2, current_dir="server"
        #   virtual: /VROOT/server  navigate 1 level up → /VROOT
        #   + "models" → /VROOT/models → normalised → "models"
        #   → correctly matches file_path_to_module key "models.py"
        virtual_base = "/VROOT/" + current_dir.as_posix()
        for _ in range(dots - 1):
            virtual_base = posixpath.dirname(virtual_base)

        if rest:
            virtual_target = virtual_base + "/" + rest.replace(".", "/")
        else:
            virtual_target = virtual_base

        normalised = posixpath.normpath(virtual_target)
        prefix = "/VROOT/"
        if normalised.startswith(prefix):
            target_posix = normalised[len(prefix):]
        elif normalised == "/VROOT":
            # dots navigated above repo root — degenerate case
            target_posix = ""
        else:
            target_posix = normalised.lstrip("/")

        # Try to find a matching module entity by file_path
        return _find_python_module_by_path(target_posix, file_path_to_module, module_entities)

    elif language_name == "typescript":
        # Normalise '../interfaces/repository.interface' relative to current dir.
        # IMPORTANT: use posixpath.normpath (always forward slashes) not os.path.normpath
        # which uses backslashes on Windows and breaks the path parsing.
        virtual_abs = "/VROOT/" + current_dir.as_posix() + "/" + module_path
        normalised_str = posixpath.normpath(virtual_abs)
        # Strip the /VROOT/ prefix (posix: "/VROOT/typescript/interfaces/repository.interface")
        prefix = "/VROOT/"
        if normalised_str.startswith(prefix):
            target_posix = normalised_str[len(prefix):]
        else:
            target_posix = normalised_str.lstrip("/")
        return _find_ts_module_by_path(target_posix, file_path_to_module)

    return None


def _resolve_absolute(
    module_path: str,
    language_name: str,
    module_entities: dict[str, Entity],
) -> Optional[str]:
    if language_name == "python":
        # Could be:  "services.auth_service"  (from python/ package root)
        # or         "models.user"
        # Try building candidate py.X.Y.Z ID
        candidate_id = "py." + module_path
        if candidate_id in module_entities:
            return candidate_id

        # Fuzzy: find any python module whose ID ends with .<last parts>
        parts = module_path.split(".")
        suffix_id = "." + ".".join(parts)
        for eid in module_entities:
            if eid.startswith("py.") and eid.endswith(suffix_id):
                return eid
        # Single-component: match any py.*.module_name
        if len(parts) == 1:
            for eid, ent in module_entities.items():
                if ent.language == "python" and eid.split(".")[-1] == parts[0]:
                    return eid
        return None

    # TypeScript absolute imports are node_modules — external
    return None


def _find_python_module_by_path(
    target_posix: str,
    file_path_to_module: dict[str, Entity],
    module_entities: dict[str, Entity],
) -> Optional[str]:
    """Match a target posix path (without extension) to a python module entity."""
    # Defensive: strip any leading "./" that can appear when the virtual-root
    # normalisation produces a path relative to the repo root (e.g. "./models").
    if target_posix.startswith("./"):
        target_posix = target_posix[2:]
    if not target_posix or target_posix == ".":
        return None

    # Try with .py extension
    candidate_file = target_posix + ".py"
    if candidate_file in file_path_to_module:
        return file_path_to_module[candidate_file].id

    # Also try as a package __init__.py
    candidate_init = target_posix + "/__init__.py"
    if candidate_init in file_path_to_module:
        return file_path_to_module[candidate_init].id

    # Fuzzy: strip leading "python/" prefix if present, then match by entity ID.
    # e.g. "python/models/base" → candidate "py.models.base"
    parts = [p for p in target_posix.split("/") if p and p != "."]
    if parts and parts[0] == "python":
        parts = parts[1:]
    if parts:
        candidate_id = "py." + ".".join(parts)
        if candidate_id in module_entities:
            return candidate_id

    return None


def _find_ts_module_by_path(
    target_posix: str,
    file_path_to_module: dict[str, Entity],
) -> Optional[str]:
    """Match a TypeScript path (without extension) to a module entity."""
    for ext in (".ts", ".tsx"):
        candidate = target_posix + ext
        if candidate in file_path_to_module:
            return file_path_to_module[candidate].id
    return None


# Keep the ID-generation helper for backwards compat with call/inheritance resolvers
def _ts_file_path_to_id(file_path: str) -> str:
    p = PurePosixPath(file_path)
    parts = list(p.parts)
    if parts and parts[0] == "typescript":
        parts = parts[1:]
    last = parts[-1] if parts else ""
    for ext in (".ts", ".tsx"):
        if last.endswith(ext):
            last = last[: -len(ext)]
            break
    last = last.replace(".", "_")
    if parts:
        parts[-1] = last
    return "ts." + ".".join(parts)
