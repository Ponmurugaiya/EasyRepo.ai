"""Language adapter registry.

To add a new language: create a new adapter in this package implementing
``LanguageAdapter``, then register it here by extension. No other file
needs to change.
"""

from src.languages.base import LanguageAdapter
from src.languages.python_adapter import PythonAdapter
from src.languages.typescript_adapter import TypeScriptAdapter

_ADAPTERS: list[LanguageAdapter] = [
    PythonAdapter(),
    TypeScriptAdapter(),
]

# Extension -> adapter  (e.g. ".py" -> PythonAdapter)
ADAPTER_REGISTRY: dict[str, LanguageAdapter] = {}
for _adapter in _ADAPTERS:
    for _ext in _adapter.file_extensions:
        ADAPTER_REGISTRY[_ext] = _adapter


def get_adapter(file_extension: str) -> LanguageAdapter | None:
    """Return the adapter for *file_extension* (e.g. ``'.py'``), or ``None``."""
    return ADAPTER_REGISTRY.get(file_extension)


__all__ = ["LanguageAdapter", "get_adapter", "ADAPTER_REGISTRY"]
