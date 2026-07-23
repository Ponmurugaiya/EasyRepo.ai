"""Authentication service providing identity verification and persistence."""

from typing import Dict, Any, Optional
from interfaces.repository import Repository
from models.user import UserModel


class AuthService(Repository):
    """Auth service handling token validation and user identity storage."""

    def __init__(self) -> None:
        """Initialize in-memory data store for authentication service."""
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(self, entity_id: str, record: Dict[str, Any]) -> bool:
        """Persist user record dictionary to internal storage."""
        self._store[entity_id] = record
        return True

    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve stored user record by entity ID."""
        return self._store.get(entity_id)

    def delete(self, entity_id: str) -> bool:
        """Remove user record from internal storage."""
        return bool(self._store.pop(entity_id, None))

    def validate(self, token: str) -> bool:
        """Validate bearer token format and authentication state (unrelated to UserModel.validate)."""
        if not token or not token.startswith("Bearer "):
            return False
        raw_token = token.split(" ")[1]
        return len(raw_token) >= 12

    def authenticate_user(self, user_id: str, email: str) -> Dict[str, Any]:
        """Authenticate user ID, construct UserModel instance, call base methods, and save record."""
        user = UserModel(entity_id=user_id, email=email)
        if not user.validate():
            raise ValueError(f"Invalid user credentials for {user_id}")
        
        user_record = user.to_dict()
        self.save(user_id, user_record)
        return user_record
