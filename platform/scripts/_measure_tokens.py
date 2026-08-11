# -*- coding: utf-8 -*-
"""Measure token count of all sample-repo entities to pick the right batch size."""
import sys
sys.path.insert(0, r'P:\EasyRepo\platform')

from pathlib import Path
import json

# Simple word-based token estimator (1 token ≈ 4 chars)
def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)

from src.extraction.entity_extractor import EntityExtractor
from src.embedding.embedder import format_entity_for_embedding

extractor = EntityExtractor()
entities, _ = extractor.extract_repository(str(Path(r'P:\EasyRepo\sample-repo')))

texts = [format_entity_for_embedding(e) for e in entities]
token_counts = [est_tokens(t) for t in texts]

total = sum(token_counts)
max_t = max(token_counts)
avg_t = total / len(token_counts)

print(f"Entities: {len(texts)}")
print(f"Total tokens (est): {total}")
print(f"Max tokens per entity: {max_t}")
print(f"Avg tokens per entity: {avg_t:.1f}")
print()

# Show how many entities fit under 10K TPM
for batch_size in [1, 2, 3, 4, 5]:
    batches = [texts[i:i+batch_size] for i in range(0, len(texts), batch_size)]
    max_batch_tokens = max(sum(est_tokens(t) for t in b) for b in batches)
    print(f"  batch_size={batch_size}: {len(batches)} batches, max_batch_tokens={max_batch_tokens}  "
          f"{'✓ under 10K' if max_batch_tokens < 10000 else '✗ over 10K'}")
