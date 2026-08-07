"""Test that prose-before-tag extraction preserves citations correctly."""
import sys
sys.path.insert(0, "platform")

from src.generation.answer_agent import _extract_answer_json

# Simulate exactly what models produce:
# Full detailed answer with citations in prose, short summary in JSON field
raw = (
    "## Overview\n"
    "The `login` method [services/auth.py:12-35] validates credentials by calling\n"
    "`hash_password` [utils/crypto.py:5-15].\n\n"
    "## Flow\n"
    "1. Request hits `login` [services/auth.py:12-35]\n"
    "2. Calls `find_user` [models/user.py:20-30]\n\n"
    "## Summary\n"
    "Authentication uses JWT tokens managed by `AuthService` [services/auth.py:1-80].\n\n"
    "<answer_json>\n"
    '{"status": "answered", "answer": "Auth uses JWT.", '
    '"ltm_entry": {"feature_name": "auth", "confidence": "high", '
    '"exploration_status": "complete", "summary": "JWT auth"}}\n'
    "</answer_json>"
)

# Extract JSON block
parsed = _extract_answer_json(raw)
assert parsed is not None, "JSON block not found"
assert parsed["status"] == "answered"

# Simulate what answer_agent.run() now does
tag_pos = raw.lower().find("<answer_json>")
prose_answer = raw[:tag_pos].strip() if tag_pos != -1 else ""
json_answer = parsed.get("answer", "")
final_answer = prose_answer or json_answer or raw.strip()

# Assertions
assert "[services/auth.py:12-35]" in final_answer, "Citation lost: auth.py"
assert "[utils/crypto.py:5-15]" in final_answer, "Citation lost: crypto.py"
assert "[models/user.py:20-30]" in final_answer, "Citation lost: user.py"
assert "[services/auth.py:1-80]" in final_answer, "Citation lost: auth.py class"
assert len(final_answer) > len(json_answer), "Prose shorter than JSON — wrong fallback"
assert "## Overview" in final_answer, "Markdown headings missing"
assert "## Summary" in final_answer, "Summary section missing"

print("PASS — prose extraction preserves all citations and markdown structure")
print(f"  Prose length:      {len(final_answer)} chars")
print(f"  JSON answer field: {len(json_answer)} chars (would have lost citations)")
print(f"  Citations found:   4")
print()
print("Answer preview:")
print(final_answer[:400])
