import json
import sys
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

# Ensure platform/ is in sys.path
PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

from src.extraction.entity_extractor import EntityExtractor


def get_entity_key(ent: Dict[str, Any]) -> Tuple[str, str, str]:
    return (ent["file_path"], ent["name"], ent["type"])


def validate():
    repo_path = PLATFORM_DIR.parent / "sample-repo"
    manifest_path = repo_path / "test-manifest.json"

    if not manifest_path.exists():
        print(f"ERROR: Manifest not found at {manifest_path}")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    manifest_entities = manifest_data.get("entities", [])
    manifest_rels = manifest_data.get("relationships", [])
    manifest_contains = [r for r in manifest_rels if r["type"] == "CONTAINS"]

    extractor = EntityExtractor()
    extracted_entities_objs, extracted_rels_objs = extractor.extract_repository(str(repo_path))

    extracted_entities = [e.model_dump(exclude={"source"}) for e in extracted_entities_objs]
    extracted_rels = [r.model_dump() for r in extracted_rels_objs]
    extracted_contains = [r for r in extracted_rels if r["type"] == "CONTAINS"]

    print("=" * 60)
    print("VALIDATION REPORT AGAINST TEST MANIFEST")
    print("=" * 60)

    # 1. Map entities by structural key: (file_path, name, type)
    manifest_ent_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    manifest_id_to_key: Dict[str, Tuple[str, str, str]] = {}
    for ent in manifest_entities:
        key = get_entity_key(ent)
        manifest_ent_map[key] = ent
        manifest_id_to_key[ent["id"]] = key

    extracted_ent_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    extracted_id_to_key: Dict[str, Tuple[str, str, str]] = {}
    for ent in extracted_entities:
        key = get_entity_key(ent)
        extracted_ent_map[key] = ent
        extracted_id_to_key[ent["id"]] = key

    manifest_keys = set(manifest_ent_map.keys())
    extracted_keys = set(extracted_ent_map.keys())

    matched_keys = manifest_keys & extracted_keys
    missing_keys = manifest_keys - extracted_keys
    extra_keys = extracted_keys - manifest_keys

    line_range_mismatches = []
    parent_mismatches = []
    docstring_mismatches = []

    for key in sorted(matched_keys):
        m_ent = manifest_ent_map[key]
        e_ent = extracted_ent_map[key]

        if m_ent["start_line"] != e_ent["start_line"] or m_ent["end_line"] != e_ent["end_line"]:
            line_range_mismatches.append(
                f"  - {key}: Manifest [{m_ent['start_line']}:{m_ent['end_line']}] vs Extracted [{e_ent['start_line']}:{e_ent['end_line']}]"
            )

        m_parent_id = m_ent.get("parent_id")
        e_parent_id = e_ent.get("parent_id")

        m_parent_key = manifest_id_to_key.get(m_parent_id) if m_parent_id else None
        e_parent_key = extracted_id_to_key.get(e_parent_id) if e_parent_id else None

        if m_parent_key != e_parent_key:
            parent_mismatches.append(
                f"  - {key}: Manifest Parent {m_parent_key} vs Extracted Parent {e_parent_key}"
            )

        if m_ent.get("has_docstring") != e_ent.get("has_docstring"):
            docstring_mismatches.append(
                f"  - {key}: Manifest has_docstring={m_ent.get('has_docstring')} vs Extracted={e_ent.get('has_docstring')}"
            )

    print(f"ENTITIES SUMMARY:")
    print(f"  - Manifest Total Entities:  {len(manifest_entities)}")
    print(f"  - Extracted Total Entities: {len(extracted_entities)}")
    print(f"  - Matched Entities:         {len(matched_keys)}")
    print(f"  - Missing Entities:         {len(missing_keys)}")
    print(f"  - Extra Entities:           {len(extra_keys)}")

    if missing_keys:
        print("\n  [!] Missing Entities in Extracted:")
        for k in sorted(missing_keys):
            print(f"      {k}")

    if extra_keys:
        print("\n  [!] Extra Entities in Extracted:")
        for k in sorted(extra_keys):
            print(f"      {k}")

    if line_range_mismatches:
        print(f"\n  [!] Line Range Mismatches ({len(line_range_mismatches)}):")
        for mismatch in line_range_mismatches:
            print(mismatch)

    if parent_mismatches:
        print(f"\n  [!] Parent Relationship Mismatches ({len(parent_mismatches)}):")
        for mismatch in parent_mismatches:
            print(mismatch)

    if docstring_mismatches:
        print(f"\n  [!] Docstring Flag Mismatches ({len(docstring_mismatches)}):")
        for mismatch in docstring_mismatches:
            print(mismatch)

    # 2. CONTAINS Relationships Structural Comparison
    def get_rel_key(rel: Dict[str, Any], id_to_key_map: Dict[str, Tuple[str, str, str]]) -> Optional[Tuple[Tuple[str, str, str], Tuple[str, str, str]]]:
        src_id = rel["source_id"]
        tgt_id = rel["target_id"]
        src_key = id_to_key_map.get(src_id)
        tgt_key = id_to_key_map.get(tgt_id)
        if src_key and tgt_key:
            return (src_key, tgt_key)
        return None

    manifest_contains_keys = set()
    for r in manifest_contains:
        rk = get_rel_key(r, manifest_id_to_key)
        if rk:
            manifest_contains_keys.add(rk)

    extracted_contains_keys = set()
    for r in extracted_contains:
        rk = get_rel_key(r, extracted_id_to_key)
        if rk:
            extracted_contains_keys.add(rk)

    matched_contains = manifest_contains_keys & extracted_contains_keys
    missing_contains = manifest_contains_keys - extracted_contains_keys
    extra_contains = extracted_contains_keys - manifest_contains_keys

    print("\nCONTAINS RELATIONSHIPS SUMMARY:")
    print(f"  - Manifest CONTAINS Rels:  {len(manifest_contains)}")
    print(f"  - Extracted CONTAINS Rels: {len(extracted_contains)}")
    print(f"  - Matched CONTAINS Rels:   {len(matched_contains)}")
    print(f"  - Missing CONTAINS Rels:   {len(missing_contains)}")
    print(f"  - Extra CONTAINS Rels:     {len(extra_contains)}")

    if missing_contains:
        print("\n  [!] Missing CONTAINS Relationships:")
        for mc in sorted(missing_contains):
            print(f"      Source: {mc[0]} -> Target: {mc[1]}")

    if extra_contains:
        print("\n  [!] Extra CONTAINS Relationships:")
        for ec in sorted(extra_contains):
            print(f"      Source: {ec[0]} -> Target: {ec[1]}")

    entity_match_rate = (len(matched_keys) / len(manifest_entities)) * 100 if manifest_entities else 0
    contains_match_rate = (len(matched_contains) / len(manifest_contains)) * 100 if manifest_contains else 0

    print("\n" + "=" * 60)
    print(f"FINAL MATCH RATES:")
    print(f"  - Entities Match Rate:        {entity_match_rate:.2f}%")
    print(f"  - CONTAINS Rel Match Rate:    {contains_match_rate:.2f}%")
    print(f"  - Line Range Mismatches:      {len(line_range_mismatches)}")
    print(f"  - Parent Structure Mismatches: {len(parent_mismatches)}")
    print("=" * 60)

    is_success = (
        len(missing_keys) == 0
        and len(extra_keys) == 0
        and len(line_range_mismatches) == 0
        and len(parent_mismatches) == 0
        and len(missing_contains) == 0
        and len(extra_contains) == 0
    )

    if is_success:
        print("\nSUCCESS: 100% Match on Entities, Line Ranges, and CONTAINS Relationships!")
        sys.exit(0)
    else:
        print("\nFAILURE: Mismatches detected. See details above.")
        sys.exit(1)


if __name__ == "__main__":
    validate()
