"""Embedding configuration settings.

Model selection rationale
-------------------------
We use ``jinaai/jina-embeddings-v2-base-code`` instead of a general-purpose text
model (e.g. all-MiniLM-L6-v2) for the following reasons:

1. **Code-specific training**: Pre-trained on the GitHub code corpus with the JinaBERT
   backbone, then fine-tuned on 150 million+ docstring↔source-code pairs across 30
   programming languages (Python, TypeScript, Java, Go, Ruby, PHP, …).  This is
   exactly the retrieval signal we need: the model maps natural-language descriptions
   of behaviour directly onto the code that implements it.

2. **Sentence-transformers compatible**: Loads with ``SentenceTransformer(model_name,
   trust_remote_code=True)``.  No custom forward-pass wrappers needed; only the
   ``trust_remote_code`` flag is required because Jina ships its ALiBi-based attention
   implementation alongside the weights.

3. **Long context (8 192 tokens)**: Handles entire function bodies and class
   definitions without truncation — far better than MiniLM's 256-token window.

4. **Reasonable MVP footprint**: 161M parameters / 768 dimensions.  Compared to
   ``nomic-ai/nomic-embed-code`` (7B params, ~4 096 dims) this is 43× smaller while
   still training on code-retrieval tasks; compared to ``codet5p-110m-embedding``
   (256 dims, no sentence-transformers native support) it is far easier to integrate.

Embedding dimension: 768 (dense, L2-normalized by the model internally).
"""

MODEL_NAME = "jinaai/jina-embeddings-v2-base-code"
EMBEDDING_DIM = 768
BATCH_SIZE = 16   # Smaller batch than MiniLM: jina model is ~7× heavier per token
