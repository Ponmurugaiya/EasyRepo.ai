"""User model module implementing user domain entity logic."""

from typing import Dict, Any
from .base import BaseModel


class UserModel(BaseModel):
    """User entity representing registered users in the platform."""

    def __init__(self, entity_id: str, email: str, role: str = "user") -> None:
        """Initialize user model instance with email and role attributes."""
        super().__init__(entity_id)
        self.email = email
        self.role = role

    def to_dict(self) -> Dict[str, Any]:
        """Serialize user model instance including base model attributes."""
        base_data = super().to_dict()
        base_data.update({
            "email": self.email,
            "role": self.role,
        })
        return base_data

    def validate(self) -> bool:
        """Validate user entity fields ensuring valid email and ID presence."""
        is_id_valid = bool(self.entity_id and len(self.entity_id) > 2)
        is_email_valid = "@" in self.email and "." in self.email
        return is_id_valid and is_email_valid

    def format_user_details(self) -> str:
        """Return formatted string summary of user details."""
        return f"User({self.entity_id}) <{self.email}> [{self.role}]"
