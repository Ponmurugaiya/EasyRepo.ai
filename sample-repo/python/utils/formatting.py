"""Standalone formatting utilities for string and payload representation."""

from typing import Dict, Any, List


def format_user_record(data: Dict[str, Any], prefix: str = "USER") -> str:
    """Format raw user dictionary properties into structured display strings."""
    if not data:
        return f"[{prefix}] EMPTY_RECORD"

    lines: List[str] = [f"=== {prefix} DETAILS ==="]
    for key, value in data.items():
        formatted_key = key.strip().upper()
        formatted_val = str(value).strip()
        lines.append(f"  {formatted_key}: {formatted_val}")

    lines.append(f"=== END {prefix} ===")
    return "\n".join(lines)


def format_audit_log(data: Dict[str, Any], prefix: str = "LOG") -> str:
    """Format system audit event properties into structured log entries."""
    if not data:
        return f"[{prefix}] EMPTY_LOG"

    lines: List[str] = [f"=== {prefix} EVENT ==="]
    for key, value in data.items():
        formatted_key = key.strip().upper()
        formatted_val = str(value).strip()
        lines.append(f"  {formatted_key}: {formatted_val}")

    lines.append(f"=== END {prefix} ===")
    return "\n".join(lines)


def truncate_text(content: str, max_length: int = 50) -> str:
    """Truncate text content to maximum length appending ellipsis if exceeded."""
    if len(content) <= max_length:
        return content
    return content[: max_length - 3] + "..."
