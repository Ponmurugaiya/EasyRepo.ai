"""Admin model module extending user model with administrative privileges."""

from typing import Dict, Any, List
from .user import UserModel as BaseUserEntity


class AdminUser(BaseUserEntity):
    """Admin entity possessing elevated system permissions."""

    def __init__(self, entity_id: str, email: str, permissions: List[str]) -> None:
        """Initialize admin user with specific access permissions list."""
        super().__init__(entity_id=entity_id, email=email, role="admin")
        self.permissions = permissions

    def to_dict(self) -> Dict[str, Any]:
        """Serialize admin model attributes combining parent user attributes."""
        data = super().to_dict()
        data["permissions"] = self.permissions
        return data

    def has_permission(self, permission: str) -> bool:
        """Check whether admin user holds specific permission flag."""
        return permission in self.permissions or "all" in self.permissions

    def validate(self) -> bool:
        """Validate admin model ensuring parent validity and permissions exist."""
        parent_valid = super().validate()
        return parent_valid and len(self.permissions) > 0
