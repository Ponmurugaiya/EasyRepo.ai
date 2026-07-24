import argparse
import json
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


if __name__ == "__main__":
    main()


