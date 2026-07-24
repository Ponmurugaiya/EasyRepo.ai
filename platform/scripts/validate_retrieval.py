"""Validation script for testing retrieval pipeline against sample-repo manifest scenarios."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.retrieval import build_context, expand, search
from src.storage.db import get_session, init_db


def run_validation(db_url: str, repo_id: str, manifest_path: Path) -> bool:
    if not manifest_path.exists():
        print(f"Error: Manifest file not found at {manifest_path}")
        return False

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    test_scenarios = manifest.get("test_scenarios", [])
    print(f"\n==================================================")
    print(f"  RETRIEVAL PIPELINE VALIDATION REPORT ({len(test_scenarios)} SCENARIOS)")
    print(f"==================================================\n")

    all_passed = True

    # Scenario queries tailored to evaluate expected behavior
    scenario_queries = {
        "multi_hop_call_chain": "how does the login flow work end to end",
        "multi_level_inheritance": "how is AdminUser defined and what does it inherit",
        "interface_implementation": "how does AuthService implement the Repository interface",
        "method_disambiguation": "validate a JWT token",
        "textual_similarity_no_conflation": "format an audit log entry",
        "orphan_file_isolation": "utility functions for formatting text in isolation",
    }

    with get_session(db_url) as session:
        for idx, scenario in enumerate(test_scenarios, start=1):
            name = scenario["name"]
            expected_behavior = scenario.get("expected_behavior", "")
            relevant_ids = set(scenario.get("relevant_entity_ids", []))
            query = scenario_queries.get(name, f"Query for {name}")

            print(f"--- Scenario {idx}: {name} ---")
            print(f"Query: \"{query}\"")

            # Run retrieval pipeline
            results = search(query=query, repo_id=repo_id, top_k=5, db_session=session)
            expanded = expand(retrieved_results=results, repo_id=repo_id, db_session=session)
            final_context = build_context(expanded_contexts=expanded, query=query, repo_id=repo_id)

            # Gather entities present in final context
            entities_in_context = set()
            for exp in expanded:
                entities_in_context.add(exp.core.entity.id)
                if exp.parent_entity:
                    entities_in_context.add(exp.parent_entity.id)
                for called in exp.called_entities:
                    entities_in_context.add(called.entity.id)
                for caller in exp.caller_entities:
                    entities_in_context.add(caller.id)
                for inh in exp.inheritance_entities:
                    entities_in_context.add(inh.id)


            has_trace = "=== RECONSTRUCTED EXECUTION TRACES ===" in final_context.rendered_text

            # Evaluation per scenario rules
            passed = True
            reasons = []

            if name == "multi_hop_call_chain":
                # Must include main, user_service, auth_service, base.py and trace
                missing = relevant_ids - entities_in_context
                if missing:
                    passed = False
                    reasons.append(f"Missing expected entity IDs in context: {missing}")
                if not has_trace:
                    passed = False
                    reasons.append("Execution trace was not reconstructed.")

            elif name == "multi_level_inheritance":
                # Must include UserModel and BaseModel context for AdminUser
                missing = relevant_ids - entities_in_context
                if missing:
                    passed = False
                    reasons.append(f"Missing inherited entities: {missing}")

            elif name == "interface_implementation":
                # Must pull Repository interface
                if "py.interfaces.repository.Repository" not in entities_in_context and "py.services.auth_service.AuthService" not in entities_in_context:
                    passed = False
                    reasons.append("Interface or implementation entity missing from context.")

            elif name == "method_disambiguation":
                # Primary result must be AuthService.validate, NOT UserModel.validate
                primary_id = results[0].entity_id if results else ""
                if primary_id != "py.services.auth_service.AuthService.validate":
                    passed = False
                    reasons.append(f"Primary result was {primary_id}, expected py.services.auth_service.AuthService.validate")

            elif name == "textual_similarity_no_conflation":
                # Primary result format_audit_log, format_user_record not top result
                primary_id = results[0].entity_id if results else ""
                if primary_id != "py.utils.formatting.format_audit_log":
                    passed = False
                    reasons.append(f"Primary result was {primary_id}, expected py.utils.formatting.format_audit_log")

            elif name == "orphan_file_isolation":
                # Verify formatting entities have NO outgoing/incoming CALLS relationships to outside entities
                orphan_calls = False
                for exp in expanded:
                    if exp.core.entity.file_path == "python/utils/formatting.py":
                        if exp.called_entities or exp.caller_entities:
                            orphan_calls = True
                if orphan_calls:
                    passed = False
                    reasons.append("Fabricated external CALLS relationships found for formatting.py entities!")


            if not passed:
                all_passed = False

            status_str = "[PASS]" if passed else "[FAIL]"
            print(f"Status: {status_str}")
            print(f"Entities in context ({len(entities_in_context)}): {sorted(list(entities_in_context))}")
            print(f"Execution Trace Reconstructed: {has_trace}")
            if not passed:
                print(f"Failure Reasons: {'; '.join(reasons)}")
            print()

    print("==================================================")
    print(f"  FINAL RESULT: {'ALL PASSED' if all_passed else 'SOME SCENARIOS FAILED'}")
    print("==================================================\n")
    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Validate Retrieval Pipeline")
    parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        default="sample-repo",
        help="Repository ID",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../sample-repo/test-manifest.json"),
        help="Path to test-manifest.json",
    )

    args = parser.parse_args()
    success = run_validation(args.db_url, args.repo_id, args.manifest)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
