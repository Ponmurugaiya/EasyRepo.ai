"""Repository interface module defining persistence abstraction layer."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class Repository(ABC):
    """Abstract interface for data repository implementations."""

    @abstractmethod
    def save(self, entity_id: str, record: Dict[str, Any]) -> bool:
        """Persist record data associated with given entity ID."""
        pass

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve entity record dictionary matching provided entity ID."""
        pass

    @abstractmethod
    def delete(self, entity_id: str) -> bool:
        """Remove record corresponding to entity ID from data storage."""
        pass
