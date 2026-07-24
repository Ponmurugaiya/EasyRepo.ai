# EasyRepo AI Platform Backend

AI-powered codebase intelligence platform with graph-retrieval context expansion and citation grounding.

## Architecture

- **Extraction**: Multi-language Tree-sitter parser (Python, TypeScript) extracting AST entities & CONTAINS relationships.
- **Resolution**: Cross-file relationship resolver for `CALLS`, `INHERITS`, and `IMPLEMENTS` edges.
- **Storage**: PostgreSQL with `pgvector` for vector similarity embeddings (jinaai/jina-embeddings-v2-base-code).
- **Retrieval**: Vector search + graph-expansion (`CONTAINS`, `CALLS` depth 3, `INHERITANCE` depth 2).
- **Generation**: Gemini 2.5 Flash grounded answer engine.
- **Validation**: 3-way post-hoc citation validator (Definition, Call-Site, Unsupported).

## Known Limitations & Future Work

1. **Object Instantiation Modeling (`INSTANTIATES`)**:
   - The current relationship schema extracts `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, and `IMPLEMENTS` edges.
   - Citations referencing object instantiation lines (e.g. `UserModel created at line 37`) are classified as `unsupported` by the validator because `INSTANTIATES` is not yet a top-level relationship type in the extraction schema.
   - *Future Work*: Add `INSTANTIATES` relationship extraction to Tree-sitter language adapters alongside expanded language support (Go, Rust, Java).
