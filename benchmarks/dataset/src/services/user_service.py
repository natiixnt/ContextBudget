from __future__ import annotations

"""User business logic layer."""

from typing import Optional

from src.db.repository import UserRepository
from src.models.user import User, UserRole
from src.utils.validators import validate_email, validate_display_name


class UserNotFoundError(Exception):
    pass


class EmailAlreadyTakenError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class UserService:
    def __init__(self, repo: Optional[UserRepository] = None) -> None:
        self._repo = repo or UserRepository()

    def register(
        self,
        email: str,
        display_name: str,
        password: str,
        role: UserRole = UserRole.MEMBER,
    ) -> User:
        validate_email(email)
        validate_display_name(display_name)
        existing = self._repo.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyTakenError(f"Email already in use: {email}")
        user = User(email=email, display_name=display_name, role=role)
        user.set_password(password)
        return self._repo.create(user)

    def login(self, email: str, password: str) -> User:
        user = self._repo.get_by_email(email)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid email or password")
        if not user.check_password(password):
            raise InvalidCredentialsError("Invalid email or password")
        user.record_login()
        self._repo.update(user)
        return user

    def get(self, user_id: str) -> User:
        user = self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"User not found: {user_id}")
        return user

    def list_users(self, active_only: bool = True) -> list[User]:
        return self._repo.list_all(active_only=active_only)

    def update_display_name(self, user_id: str, display_name: str) -> User:
        validate_display_name(display_name)
        user = self.get(user_id)
        user.display_name = display_name
        return self._repo.update(user)

    def change_role(self, user_id: str, role: UserRole) -> User:
        user = self.get(user_id)
        user.role = role
        return self._repo.update(user)

    def deactivate(self, user_id: str) -> User:
        user = self.get(user_id)
        user.deactivate()
        return self._repo.update(user)
