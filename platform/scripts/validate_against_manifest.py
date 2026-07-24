import json
import sys
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional, Set

# Ensure platform/ is in sys.path
PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

from src.extraction.entity_extractor import EntityExtractor
from src.resolution import resolve_relationships
from src.languages import ADAPTER_REGISTRY


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

    # ---------------------------------------------------------------
    # Extract
    # ---------------------------------------------------------------
    extractor = EntityExtractor()
    extracted_entities_objs, extracted_contains_objs = extractor.extract_repository(
        str(repo_path)
    )
    semantic_rels_objs = resolve_relationships(
        entities=extracted_entities_objs,
        repo_root=str(repo_path),
        adapter_registry=ADAPTER_REGISTRY,
    )

    extracted_entities = [e.model_dump(exclude={"source"}) for e in extracted_entities_objs]
    all_extracted_rels = [r.model_dump() for r in extracted_contains_objs + semantic_rels_objs]

    print("=" * 60)
    print("VALIDATION REPORT AGAINST TEST MANIFEST")
    print("=" * 60)

    # ---------------------------------------------------------------
    # Entity key maps
    # ---------------------------------------------------------------
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
                f"  - {key}: Manifest [{m_ent['start_line']}:{m_ent['end_line']}] "
                f"vs Extracted [{e_ent['start_line']}:{e_ent['end_line']}]"
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
                f"  - {key}: Manifest has_docstring={m_ent.get('has_docstring')} "
                f"vs Extracted={e_ent.get('has_docstring')}"
            )

    print("ENTITIES SUMMARY:")
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

    # ---------------------------------------------------------------
    # Relationship comparison helper
    # ---------------------------------------------------------------
    def make_rel_key(
        rel: Dict[str, Any],
        id_to_key: Dict[str, Tuple[str, str, str]],
    ) -> Optional[Tuple]:
        src_key = id_to_key.get(rel["source_id"])
        tgt_key = id_to_key.get(rel["target_id"])
        if src_key and tgt_key:
            return (rel["type"], src_key, tgt_key)
        return None

    def compare_rel_type(
        rel_type: str,
        manifest_rels_all: List[Dict[str, Any]],
        extracted_rels_all: List[Dict[str, Any]],
        manifest_id_to_key: Dict[str, Tuple[str, str, str]],
        extracted_id_to_key: Dict[str, Tuple[str, str, str]],
    ) -> Tuple[int, int, int, int, List[str], List[str]]:
        """Returns matched, missing, extra counts and detail lists."""
        m_rels = [r for r in manifest_rels_all if r["type"] == rel_type]
        e_rels = [r for r in extracted_rels_all if r["type"] == rel_type]

        m_keys: Set[Tuple] = set()
        for r in m_rels:
            k = make_rel_key(r, manifest_id_to_key)
            if k:
                m_keys.add(k)

        e_keys: Set[Tuple] = set()
        for r in e_rels:
            k = make_rel_key(r, extracted_id_to_key)
            if k:
                e_keys.add(k)

        matched = m_keys & e_keys
        missing = m_keys - e_keys
        extra = e_keys - m_keys

        missing_strs = [f"      {k[1]} -> {k[2]}" for k in sorted(missing, key=str)]
        extra_strs = [f"      {k[1]} -> {k[2]}" for k in sorted(extra, key=str)]

        return len(m_rels), len(e_rels), len(matched), len(missing), missing_strs, extra_strs

    # ---------------------------------------------------------------
    # Compare each relationship type
    # ---------------------------------------------------------------
    rel_types = ["CONTAINS", "IMPORTS", "CALLS", "INHERITS", "IMPLEMENTS"]
    print()
    all_rel_success = True
    type_results = {}

    for rtype in rel_types:
        m_total, e_total, matched, missing_count, missing_strs, extra_strs = compare_rel_type(
            rtype, manifest_rels, all_extracted_rels, manifest_id_to_key, extracted_id_to_key
        )
        type_results[rtype] = (m_total, e_total, matched, missing_count, len(extra_strs))
        match_rate = (matched / m_total * 100) if m_total else 100.0

        print(f"{rtype} RELATIONSHIPS:")
        print(f"  - Manifest:  {m_total}  |  Extracted: {e_total}  |  "
              f"Matched: {matched}  |  Missing: {missing_count}  |  Extra: {len(extra_strs)}")
        print(f"  - Match Rate: {match_rate:.1f}%")

        if missing_strs:
            print(f"  [MISSING]:")
            for s in missing_strs:
                print(s)
        if extra_strs:
            print(f"  [EXTRA]:")
            for s in extra_strs:
                print(s)
        print()

        if missing_count > 0 or len(extra_strs) > 0:
            if rtype in ("CONTAINS", "CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS"):
                all_rel_success = False

    # ---------------------------------------------------------------
    # Final summary
    # ---------------------------------------------------------------
    entity_match_rate = (len(matched_keys) / len(manifest_entities) * 100) if manifest_entities else 0
    is_entity_success = (
        len(missing_keys) == 0
        and len(extra_keys) == 0
        and len(line_range_mismatches) == 0
        and len(parent_mismatches) == 0
    )

    print("=" * 60)
    print("FINAL MATCH RATES:")
    print(f"  - Entities Match Rate:         {entity_match_rate:.2f}%")
    for rtype in rel_types:
        m_total, e_total, matched, missing_count, extra_count = type_results[rtype]
        rate = (matched / m_total * 100) if m_total else 100.0
        print(f"  - {rtype:12s} Match Rate:  {rate:.1f}%  "
              f"(manifest={m_total}, extracted={e_total}, matched={matched})")
    print(f"  - Line Range Mismatches:       {len(line_range_mismatches)}")
    print(f"  - Parent Structure Mismatches: {len(parent_mismatches)}")
    print("=" * 60)

    if is_entity_success and all_rel_success:
        print("\nSUCCESS: 100% Match on all relationship types!")
        sys.exit(0)
    elif is_entity_success:
        print("\nPARTIAL SUCCESS: Entities + CONTAINS at 100%. See relationship details above.")
        sys.exit(0)
    else:
        print("\nFAILURE: Mismatches detected. See details above.")
        sys.exit(1)


if __name__ == "__main__":
    validate()
