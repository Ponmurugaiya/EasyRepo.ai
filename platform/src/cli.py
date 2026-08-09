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
        "--top-k", type=int, default=20, help="Top-K vector search results"
    )

    # ── ask subcommand ──────────────────────────────────────────────────────
    ask_parser = subparsers.add_parser(
        "ask",
        help=(
            "Run full pipeline: retrieval → LLM generation → citation validation. "
            "Uses Groq (multi-model rotation) by default, falls back to Gemini."
        ),
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
        "--top-k", type=int, default=20, help="Top-K vector search results"
    )
    # ── Groq options ──────────────────────────────────────────────────────
    ask_parser.add_argument(
        "--groq-key",
        type=str,
        default=None,
        help="Groq API key (overrides GROQ_API_KEY env var)",
    )
    ask_parser.add_argument(
        "--groq-model",
        type=str,
        default=None,
        help=(
            "Specific Groq model to use (e.g. llama-3.3-70b-versatile). "
            "Omit to rotate through all Groq models automatically."
        ),
    )
    # ── Gemini options (fallback / explicit) ──────────────────────────────
    ask_parser.add_argument(
        "--gemini-key",
        type=str,
        default=None,
        help="Gemini API key (overrides GEMINI_API_KEY env var)",
    )
    ask_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            "Force a specific model, bypassing the provider cascade. "
            "Prefix with 'groq:' or 'gemini:' to be explicit, "
            "e.g. --model gemini:gemini-2.5-flash"
        ),
    )
    ask_parser.add_argument(
        "--provider",
        type=str,
        choices=["groq", "gemini", "cerebras", "openrouter", "cohere", "cloudflare", "auto"],
        default="auto",
        help=(
            "LLM provider: 'auto' (default) tries all configured free providers in cascade order: "
            "Groq → Gemini → Cerebras → OpenRouter → Cohere → Cloudflare."
        ),
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
        from src.generation.citation_validator import validate_citations, collect_context_entities
        from src.generation.llm_client import (
            generate_answer_with_fallback,
            LLMProviderError,
            GROQ_MODEL_NAMES,
        )

        # ── Resolve API keys ─────────────────────────────────────────────
        if args.groq_key:
            os.environ["GROQ_API_KEY"] = args.groq_key
        if args.gemini_key:
            os.environ["GEMINI_API_KEY"] = args.gemini_key

        groq_key        = os.environ.get("GROQ_API_KEY", "")
        gemini_key      = os.environ.get("GEMINI_API_KEY", "")
        cerebras_key    = os.environ.get("CEREBRAS_API_KEY", "")
        openrouter_key  = os.environ.get("OPENROUTER_API_KEY", "")
        cohere_key      = os.environ.get("COHERE_API_KEY", "")
        cloudflare_key  = os.environ.get("CLOUDFLARE_API_KEY", "")

        # ── Parse provider / model flags ────────────────────────────────
        force_groq_model: str | None = args.groq_model
        force_gemini_model: str = "gemini-2.5-flash"

        # When a specific provider is forced, skip all others
        provider_arg = args.provider  # "auto", "groq", "gemini", "cerebras", etc.
        skip_groq       = provider_arg not in ("auto", "groq")
        skip_gemini     = provider_arg not in ("auto", "gemini")
        skip_cerebras   = provider_arg not in ("auto", "cerebras")
        skip_openrouter = provider_arg not in ("auto", "openrouter")
        skip_cohere     = provider_arg not in ("auto", "cohere")
        skip_cloudflare = provider_arg not in ("auto", "cloudflare")

        if args.model:
            if args.model.startswith("groq:"):
                force_groq_model = args.model[len("groq:"):]
                skip_gemini = skip_cerebras = skip_openrouter = skip_cohere = skip_cloudflare = True
            elif args.model.startswith("gemini:"):
                force_gemini_model = args.model[len("gemini:"):]
                skip_groq = skip_cerebras = skip_openrouter = skip_cohere = skip_cloudflare = True
            elif args.model in GROQ_MODEL_NAMES:
                force_groq_model = args.model
            else:
                force_gemini_model = args.model
                skip_groq = skip_cerebras = skip_openrouter = skip_cohere = skip_cloudflare = True

        # Auto-skip providers with no key
        if not groq_key:        skip_groq = True
        if not gemini_key:      skip_gemini = True
        if not cerebras_key:    skip_cerebras = True
        if not openrouter_key:  skip_openrouter = True
        if not cohere_key:      skip_cohere = True
        if not cloudflare_key:  skip_cloudflare = True

        if all([skip_groq, skip_gemini, skip_cerebras, skip_openrouter, skip_cohere, skip_cloudflare]):
            print(
                "ERROR: No LLM API keys configured.\n"
                "  Set at least one of: GROQ_API_KEY, GEMINI_API_KEY, CEREBRAS_API_KEY,\n"
                "  OPENROUTER_API_KEY, COHERE_API_KEY, or CLOUDFLARE_API_KEY.",
                file=sys.stderr,
            )
            sys.exit(1)

        # ── Provider summary ─────────────────────────────────────────────
        active_providers = []
        if not skip_groq:
            active_providers.append(f"Groq ({force_groq_model or 'auto-rotate'})")
        if not skip_gemini:
            active_providers.append(f"Gemini ({force_gemini_model})")
        if not skip_cerebras:
            active_providers.append("Cerebras")
        if not skip_openrouter:
            active_providers.append("OpenRouter")
        if not skip_cohere:
            active_providers.append("Cohere")
        if not skip_cloudflare:
            active_providers.append("Cloudflare")
        print(f"[*] Provider cascade: {' → '.join(active_providers)}")

        print(f"[*] Running retrieval for repo '{args.repo_id}'...")
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

            # Step 5 — Generate answer (full free-tier cascade)
            try:
                answer, provider_used, _, _ = generate_answer_with_fallback(
                    query=args.question,
                    context=user_prompt,
                    system_prompt=system_prompt,
                    groq_model=force_groq_model,
                    groq_api_key=groq_key or None,
                    gemini_model=force_gemini_model,
                    gemini_api_key=gemini_key or None,
                    skip_groq=skip_groq,
                    skip_gemini=skip_gemini,
                    skip_cerebras=skip_cerebras,
                    skip_openrouter=skip_openrouter,
                    skip_cohere=skip_cohere,
                    skip_cloudflare=skip_cloudflare,
                )
                print(f"[*] Answer generated via {provider_used.upper()}.\n")
            except LLMProviderError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                sys.exit(1)

            # Step 6 — Citation validation (3-way classification)
            context_entities = collect_context_entities(final_context)
            report = validate_citations(
                answer=answer,
                context_entities=context_entities,
                final_context=final_context,
                db_session=session,
            )

            # ── Output ──────────────────────────────────────────────────────
            print("=" * 70)
            print("ANSWER")
            print("=" * 70)
            print(answer)
            print()
            print(report.format_report())


if __name__ == "__main__":
    main()


