"""Base model module defining abstract foundational entity structures."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any


class BaseModel(ABC):
    """Abstract base class for all system entities providing baseline metadata."""

    def __init__(self, entity_id: str) -> None:
        """Initialize base model with entity identifier and creation timestamp."""
        self.entity_id = entity_id
        self.created_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert entity base properties to dictionary representation."""
        return {
            "entity_id": self.entity_id,
            "created_at": self.created_at,
        }

    def get_metadata(self) -> Dict[str, str]:
        """Retrieve system metadata associated with entity instance."""
        return {
            "type": self.__class__.__name__,
            "timestamp": self.created_at,
        }

    @abstractmethod
    def validate(self) -> bool:
        """Validate internal entity integrity and field constraints."""
        pass
