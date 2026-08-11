# -*- coding: utf-8 -*-
import sys, json, os
sys.path.insert(0, r'P:\EasyRepo\platform')
with open(r'P:\EasyRepo\.env', encoding='utf-8') as f:
    for l in f:
        l=l.strip()
        if l and '=' in l and not l.startswith('#'):
            k,v=l.split('=',1); os.environ.setdefault(k.strip(),v.strip())

import logging
logging.disable(logging.CRITICAL)

from src.extraction.entity_extractor import EntityExtractor
from src.resolution import resolve_relationships
from src.languages import ADAPTER_REGISTRY
from pathlib import Path

repo_path = Path(r'P:\EasyRepo\sample-repo')
extractor = EntityExtractor()
entities, contains_rels = extractor.extract_repository(str(repo_path))
sem_rels = resolve_relationships(entities, str(repo_path), ADAPTER_REGISTRY)
all_rels = contains_rels + sem_rels

# Dump all entities as JSON for manifest update
out = {
    "entities": [e.model_dump(exclude={"source"}) for e in entities],
    "relationships": [r.model_dump() for r in all_rels],
}
with open(r'P:\EasyRepo\sample-repo\_extracted_full.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, default=str)

vars_ = [e for e in entities if e.type == 'variable']
print(f"Variable entities ({len(vars_)}):")
for v in sorted(vars_, key=lambda e: e.id):
    print(f"  {{'id': '{v.id}', 'type': 'variable', 'name': '{v.name}', 'file_path': '{v.file_path}', 'start_line': {v.start_line}, 'end_line': {v.end_line}, 'parent_id': '{v.parent_id}', 'language': '{v.language}', 'has_docstring': {str(v.has_docstring).lower()}}}")

insts = [r for r in all_rels if r.type == 'INSTANTIATES']
print(f"\nINSTANTIATES ({len(insts)}):")
for r in insts:
    print(f"  {{'type': 'INSTANTIATES', 'source_id': '{r.source_id}', 'target_id': '{r.target_id}', 'file_path': '{r.file_path}', 'line': {r.line}}}")

non_py_mods = [e for e in entities if e.type == 'module' and not e.file_path.endswith('.py') and not e.file_path.endswith('.ts')]
print(f"\nNon-Python/TS module entities ({len(non_py_mods)}):")
for m in non_py_mods:
    print(f"  {m.id}  file={m.file_path}")

print(f"\nTotal: {len(entities)} entities, {len(all_rels)} relationships")
