"""User service module orchestrating authentication and profile management workflows."""

from typing import Dict, Any
from services.auth_service import AuthService


class UserService:
    """High-level application service managing user operations and session logic."""

    def __init__(self, auth_service: AuthService) -> None:
        """Inject auth service dependency into user service controller."""
        self.auth_service = auth_service

    def login_user(self, user_id: str, email: str, auth_token: str) -> Dict[str, Any]:
        """Execute user login workflow by validating auth token and authenticating identity."""
        if not self.auth_service.validate(auth_token):
            raise PermissionError("Provided auth token is invalid or expired")

        record = self.auth_service.authenticate_user(user_id, email)
        return {
            "status": "authenticated",
            "profile": record,
        }

    def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """Retrieve existing user profile record via underlying auth repository."""
        profile = self.auth_service.find_by_id(user_id)
        if not profile:
            raise KeyError(f"User profile with ID {user_id} not found")
        return profile
