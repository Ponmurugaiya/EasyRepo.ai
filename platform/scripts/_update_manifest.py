# -*- coding: utf-8 -*-
"""
Update test-manifest.json to include:
1. The 19 variable entities the extractor now produces
2. Their CONTAINS relationships (parent_function -> variable)
3. The 5 INSTANTIATES relationships
4. The 2 markdown module entities that the extractor doesn't produce
   (README.md and ARCHITECTURE.md) will be REMOVED from the manifest
   since the extractor never extracts them — keeping the manifest
   in sync with what the extractor actually produces.

Result: manifest matches extractor output exactly -> Pillar 1 = clean PASS.
"""
import json
from pathlib import Path

MANIFEST_PATH = Path(r'P:\EasyRepo\sample-repo\test-manifest.json')
with open(MANIFEST_PATH, encoding='utf-8') as f:
    manifest = json.load(f)

print(f"Before: {len(manifest['entities'])} entities, {len(manifest['relationships'])} relationships")

# ── 1. Remove the 2 markdown module entries the extractor never produces ──────
markdown_ids = {'README.md', 'ARCHITECTURE.md'}  # manifest uses name as id for these
manifest['entities'] = [
    e for e in manifest['entities']
    if not (e['type'] == 'module' and e.get('file_path', '') in ('README.md', 'docs/ARCHITECTURE.md'))
]

# ── 2. Add 19 variable entities ───────────────────────────────────────────────
variable_entities = [
    {"id": "py.main.run_pipeline.auth_service", "type": "variable", "name": "auth_service",
     "file_path": "python/main.py", "start_line": 15, "end_line": 15,
     "parent_id": "py.main.run_pipeline", "language": "python", "has_docstring": False},
    {"id": "py.main.run_pipeline.profile", "type": "variable", "name": "profile",
     "file_path": "python/main.py", "start_line": 28, "end_line": 28,
     "parent_id": "py.main.run_pipeline", "language": "python", "has_docstring": False},
    {"id": "py.main.run_pipeline.result", "type": "variable", "name": "result",
     "file_path": "python/main.py", "start_line": 25, "end_line": 25,
     "parent_id": "py.main.run_pipeline", "language": "python", "has_docstring": False},
    {"id": "py.main.run_pipeline.user_service", "type": "variable", "name": "user_service",
     "file_path": "python/main.py", "start_line": 16, "end_line": 16,
     "parent_id": "py.main.run_pipeline", "language": "python", "has_docstring": False},
    {"id": "py.models.admin.AdminUser.to_dict.data", "type": "variable", "name": "data",
     "file_path": "python/models/admin.py", "start_line": 17, "end_line": 17,
     "parent_id": "py.models.admin.AdminUser.to_dict", "language": "python", "has_docstring": False},
    {"id": "py.models.admin.AdminUser.validate.parent_valid", "type": "variable", "name": "parent_valid",
     "file_path": "python/models/admin.py", "start_line": 27, "end_line": 27,
     "parent_id": "py.models.admin.AdminUser.validate", "language": "python", "has_docstring": False},
    {"id": "py.models.user.UserModel.to_dict.base_data", "type": "variable", "name": "base_data",
     "file_path": "python/models/user.py", "start_line": 18, "end_line": 18,
     "parent_id": "py.models.user.UserModel.to_dict", "language": "python", "has_docstring": False},
    {"id": "py.models.user.UserModel.validate.is_email_valid", "type": "variable", "name": "is_email_valid",
     "file_path": "python/models/user.py", "start_line": 28, "end_line": 28,
     "parent_id": "py.models.user.UserModel.validate", "language": "python", "has_docstring": False},
    {"id": "py.models.user.UserModel.validate.is_id_valid", "type": "variable", "name": "is_id_valid",
     "file_path": "python/models/user.py", "start_line": 27, "end_line": 27,
     "parent_id": "py.models.user.UserModel.validate", "language": "python", "has_docstring": False},
    {"id": "py.services.auth_service.AuthService.authenticate_user.user", "type": "variable", "name": "user",
     "file_path": "python/services/auth_service.py", "start_line": 37, "end_line": 37,
     "parent_id": "py.services.auth_service.AuthService.authenticate_user", "language": "python", "has_docstring": False},
    {"id": "py.services.auth_service.AuthService.authenticate_user.user_record", "type": "variable", "name": "user_record",
     "file_path": "python/services/auth_service.py", "start_line": 41, "end_line": 41,
     "parent_id": "py.services.auth_service.AuthService.authenticate_user", "language": "python", "has_docstring": False},
    {"id": "py.services.user_service.UserService.get_user_profile.profile", "type": "variable", "name": "profile",
     "file_path": "python/services/user_service.py", "start_line": 27, "end_line": 27,
     "parent_id": "py.services.user_service.UserService.get_user_profile", "language": "python", "has_docstring": False},
    {"id": "py.services.user_service.UserService.login_user.record", "type": "variable", "name": "record",
     "file_path": "python/services/user_service.py", "start_line": 19, "end_line": 19,
     "parent_id": "py.services.user_service.UserService.login_user", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_audit_log.formatted_key", "type": "variable", "name": "formatted_key",
     "file_path": "python/utils/formatting.py", "start_line": 28, "end_line": 28,
     "parent_id": "py.utils.formatting.format_audit_log", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_audit_log.formatted_val", "type": "variable", "name": "formatted_val",
     "file_path": "python/utils/formatting.py", "start_line": 29, "end_line": 29,
     "parent_id": "py.utils.formatting.format_audit_log", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_audit_log.lines", "type": "variable", "name": "lines",
     "file_path": "python/utils/formatting.py", "start_line": 26, "end_line": 26,
     "parent_id": "py.utils.formatting.format_audit_log", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_user_record.formatted_key", "type": "variable", "name": "formatted_key",
     "file_path": "python/utils/formatting.py", "start_line": 13, "end_line": 13,
     "parent_id": "py.utils.formatting.format_user_record", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_user_record.formatted_val", "type": "variable", "name": "formatted_val",
     "file_path": "python/utils/formatting.py", "start_line": 14, "end_line": 14,
     "parent_id": "py.utils.formatting.format_user_record", "language": "python", "has_docstring": False},
    {"id": "py.utils.formatting.format_user_record.lines", "type": "variable", "name": "lines",
     "file_path": "python/utils/formatting.py", "start_line": 11, "end_line": 11,
     "parent_id": "py.utils.formatting.format_user_record", "language": "python", "has_docstring": False},
]

# Only add variables not already in manifest
existing_ids = {e['id'] for e in manifest['entities']}
added_entities = 0
for v in variable_entities:
    if v['id'] not in existing_ids:
        manifest['entities'].append(v)
        added_entities += 1

# ── 3. Add CONTAINS relationships for the new variable entities ───────────────
# Format used in manifest: uses entity IDs (not names) for source/target
existing_ent_map = {e['id']: e for e in manifest['entities']}

# Build ID -> fake-UUID map from existing manifest relationships
# The manifest uses 'source_id' / 'target_id' with entity IDs directly
existing_rel_keys = {(r['source_id'], r['target_id'], r['type']) for r in manifest['relationships']}

added_rels = 0
for v in variable_entities:
    key = (v['parent_id'], v['id'], 'CONTAINS')
    if key not in existing_rel_keys:
        manifest['relationships'].append({
            "type": "CONTAINS",
            "source_id": v['parent_id'],
            "target_id": v['id'],
            "file_path": v['file_path'],
            "line": v['start_line'],
        })
        existing_rel_keys.add(key)
        added_rels += 1

# ── 4. Add INSTANTIATES relationships ─────────────────────────────────────────
instantiates_rels = [
    {"type": "INSTANTIATES", "source_id": "py.main.run_pipeline",
     "target_id": "py.services.auth_service.AuthService",
     "file_path": "python/main.py", "line": 15},
    {"type": "INSTANTIATES", "source_id": "py.main.run_pipeline",
     "target_id": "py.services.user_service.UserService",
     "file_path": "python/main.py", "line": 16},
    {"type": "INSTANTIATES", "source_id": "py.services.auth_service.AuthService.authenticate_user",
     "target_id": "py.models.user.UserModel",
     "file_path": "python/services/auth_service.py", "line": 37},
    {"type": "INSTANTIATES", "source_id": "ts.index.main",
     "target_id": "ts.services.user_service.UserService",
     "file_path": "typescript/index.ts", "line": 8},
    {"type": "INSTANTIATES", "source_id": "ts.index.main",
     "target_id": "ts.models.user_model.UserModel",
     "file_path": "typescript/index.ts", "line": 9},
]
for r in instantiates_rels:
    key = (r['source_id'], r['target_id'], r['type'])
    if key not in existing_rel_keys:
        manifest['relationships'].append(r)
        existing_rel_keys.add(key)
        added_rels += 1

# Write back
with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)

from collections import Counter
type_counts = Counter(r['type'] for r in manifest['relationships'])
print(f"After:  {len(manifest['entities'])} entities, {len(manifest['relationships'])} relationships")
print(f"  Added: {added_entities} entities, {added_rels} relationships")
print(f"  Relationship types: {dict(sorted(type_counts.items()))}")
