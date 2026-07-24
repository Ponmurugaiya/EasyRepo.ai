import argparse
import json
import os
import sys
from pathlib import Path

from src.extraction.entity_extractor import EntityExtractor
from src.resolution import resolve_relationships
from src.languages import ADAPTER_REGISTRY


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Codebase Intelligence Platform CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract", help="Extract entities and relationships from a repository"
    )
    extract_parser.add_argument("repo_path", type=str, help="Path to repository directory")
    extract_parser.add_argument(
        "--output", "-o", type=str, default="entities.json", help="Output JSON file path"
    )
    extract_parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="Skip relationship resolution (emit CONTAINS only)",
    )

    ingest_parser = subparsers.add_parser(
        "ingest", help="Ingest a repository into PostgreSQL database with vector embeddings"
    )
    ingest_parser.add_argument("repo_path", type=str, help="Path to repository directory")
    ingest_parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
        help="Database connection URL",
    )

    ingest_parser.add_argument(
        "--repo-id", type=str, default=None, help="Custom repository ID"
    )
    ingest_parser.add_argument(
        "--repo-name", type=str, default=None, help="Custom repository name"
    )

    query_parser = subparsers.add_parser(
        "query", help="Run retrieval pipeline for a natural language question"
    )
    query_parser.add_argument("repo_id", type=str, help="Repository ID")
    query_parser.add_argument("question", type=str, help="Natural language question/query")
    query_parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
        help="Database connection URL",
    )
    query_parser.add_argument(
        "--top-k", type=int, default=10, help="Top-K vector search results"
    )

    # ── ask subcommand ──────────────────────────────────────────────────────
    ask_parser = subparsers.add_parser(
        "ask",
        help="Run full pipeline: retrieval → Gemini generation → citation validation",
    )
    ask_parser.add_argument("repo_id", type=str, help="Repository ID")
    ask_parser.add_argument("question", type=str, help="Natural language question")
    ask_parser.add_argument(
        "--db-url",
        type=str,
        default="postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
        help="Database connection URL",
    )
    ask_parser.add_argument(
        "--top-k", type=int, default=10, help="Top-K vector search results"
    )
    ask_parser.add_argument(
        "--gemini-key",
        type=str,
        default=None,
        help="Gemini API key (overrides GEMINI_API_KEY env var)",
    )
    ask_parser.add_argument(
        "--model",
        type=str,
        default="gemini-2.5-flash",
        help="Gemini model identifier (default: gemini-2.5-flash)",
    )
    ask_parser.add_argument(
        "--token-budget",
        type=int,
        default=6000,
        help="Context token budget for retrieval (default: 6000)",
    )

    args = parser.parse_args()

    if args.command == "extract":
        extractor = EntityExtractor()
        entities, contains_rels = extractor.extract_repository(args.repo_path)

        semantic_rels = []
        if not args.no_resolve:
            semantic_rels = resolve_relationships(
                entities=entities,
                repo_root=args.repo_path,
                adapter_registry=ADAPTER_REGISTRY,
            )

        all_relationships = contains_rels + semantic_rels

        entities_data = [e.model_dump(exclude={"source"}) for e in entities]
        relationships_data = [r.model_dump() for r in all_relationships]

        output_data = {
            "entities": entities_data,
            "relationships": relationships_data,
        }

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)

        rel_by_type: dict[str, int] = {}
        for r in all_relationships:
            rel_by_type[r.type] = rel_by_type.get(r.type, 0) + 1

        print(
            f"Extracted {len(entities)} entities and {len(all_relationships)} relationships "
            f"to {output_path}"
        )
        for rtype, count in sorted(rel_by_type.items()):
            print(f"  {rtype}: {count}")

    elif args.command == "ingest":
        from src.ingestion.pipeline import ingest_repository
        from src.storage.db import get_session, init_db

        init_db(args.db_url)
        with get_session(args.db_url) as session:
            repo = ingest_repository(
                repo_path_or_url=args.repo_path,
                db_session=session,
                repo_id=args.repo_id,
                repo_name=args.repo_name,
            )
            print(f"Successfully ingested repository '{repo.name}' (ID: {repo.id}) with status '{repo.status}'.")

    elif args.command == "query":
        from src.retrieval import build_context, expand, search
        from src.storage.db import get_session

        with get_session(args.db_url) as session:
            results = search(
                query=args.question,
                repo_id=args.repo_id,
                top_k=args.top_k,
                db_session=session,
            )
            expanded = expand(
                retrieved_results=results,
                repo_id=args.repo_id,
                db_session=session,
            )
            final_context = build_context(
                expanded_contexts=expanded,
                query=args.question,
                repo_id=args.repo_id,
            )
            print(final_context.rendered_text)

    elif args.command == "ask":
        from src.retrieval import build_context, expand, search
        from src.storage.db import get_session
        from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
        from src.generation.gemini_client import generate_answer, GeminiClientError
        from src.generation.citation_validator import validate_citations, collect_context_entities

        # Resolve API key: --gemini-key flag takes precedence over env var
        if args.gemini_key:
            os.environ["GEMINI_API_KEY"] = args.gemini_key

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print(
                "ERROR: No Gemini API key found.\n"
                "  Set the GEMINI_API_KEY environment variable or pass --gemini-key.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"\n[*] Running retrieval for repo '{args.repo_id}'...")
        with get_session(args.db_url) as session:
            # Step 1 — Vector search
            results = search(
                query=args.question,
                repo_id=args.repo_id,
                top_k=args.top_k,
                db_session=session,
            )
            print(f"    Retrieved {len(results)} candidate entities.")

            # Step 2 — Graph expansion
            expanded = expand(
                retrieved_results=results,
                repo_id=args.repo_id,
                db_session=session,
            )
            print(f"    Expanded to {len(expanded)} context blocks.")

            # Step 3 — Context assembly
            final_context = build_context(
                expanded_contexts=expanded,
                query=args.question,
                repo_id=args.repo_id,
                token_budget=args.token_budget,
            )
            print(
                f"    Context assembled (~{final_context.total_tokens_est} tokens"
                f"{', truncated' if final_context.truncated else ''}).\n"
            )

            # Step 4 — Render structured user-turn prompt
            system_prompt = build_system_prompt()
            user_prompt = render_context_for_prompt(final_context)

            # Step 5 — Generate answer via Gemini
            print(f"[*] Calling Gemini ({args.model})...\n")
            try:
                answer = generate_answer(
                    query=args.question,
                    context=user_prompt,
                    system_prompt=system_prompt,
                    model=args.model,
                    api_key=api_key,
                )
            except GeminiClientError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                sys.exit(1)

            # Step 6 — Citation validation
            context_entities = collect_context_entities(final_context)
            report = validate_citations(answer, context_entities)

            # ── Output ──────────────────────────────────────────────────────
            print("=" * 70)
            print("ANSWER")
            print("=" * 70)
            print(answer)
            print()
            print(report.format_report())


if __name__ == "__main__":
    main()


