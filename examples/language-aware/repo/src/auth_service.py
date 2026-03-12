import os
import hashlib

# Handles auth checks.
class AuthService:
    """Auth service docs."""

    def login(self, token: str) -> bool:
        return token.startswith("prod_")


def helper() -> None:
    pass
