"""Main entrypoint script driving Python service execution and call chain validation."""

import sys
from pathlib import Path

# Ensure python directory is in sys.path when executed directly
sys.path.insert(0, str(Path(__file__).parent))

from services.auth_service import AuthService
from services.user_service import UserService


def run_pipeline() -> None:
    """Execute main execution pipeline demonstrating cross-service call chain."""
    auth_service = AuthService()
    user_service = UserService(auth_service=auth_service)

    user_id = "usr_9981"
    email = "admin@enterprise.org"
    token = "Bearer secret_access_token_value_xyz123"

    # Hop 1: main -> user_service (login_user)
    # Hop 2: user_service -> auth_service (authenticate_user & validate)
    # Hop 3: auth_service -> base.py (UserModel.to_dict calling BaseModel.to_dict)
    result = user_service.login_user(user_id=user_id, email=email, auth_token=token)
    print("Execution Result:", result)

    profile = user_service.get_user_profile(user_id=user_id)
    print("Retrieved Profile:", profile)


if __name__ == "__main__":
    run_pipeline()
