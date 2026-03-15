from __future__ import annotations

"""User domain model."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import hashlib
import uuid


class UserRole(str, Enum):
    VIEWER = "viewer"
    MEMBER = "member"
    ADMIN = "admin"


@dataclass
class User:
    email: str
    display_name: str
    role: UserRole = UserRole.MEMBER
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True
    last_login_at: Optional[datetime] = None
    _password_hash: Optional[str] = field(default=None, repr=False)

    def set_password(self, plaintext: str) -> None:
        self._password_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        self.updated_at = datetime.now(timezone.utc)

    def check_password(self, plaintext: str) -> bool:
        if self._password_hash is None:
            return False
        return self._password_hash == hashlib.sha256(plaintext.encode()).hexdigest()

    def record_login(self) -> None:
        self.last_login_at = datetime.now(timezone.utc)

    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    def deactivate(self) -> None:
        self.is_active = False
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self, include_sensitive: bool = False) -> dict:
        d: dict = {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role.value,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }
        if include_sensitive:
            d["password_hash"] = self._password_hash
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        u = cls(
            id=data.get("id", str(uuid.uuid4())),
            email=data["email"],
            display_name=data.get("display_name", data["email"]),
            role=UserRole(data.get("role", UserRole.MEMBER.value)),
            is_active=data.get("is_active", True),
        )
        if data.get("password_hash"):
            u._password_hash = data["password_hash"]
        if data.get("last_login_at"):
            u.last_login_at = datetime.fromisoformat(data["last_login_at"])
        if data.get("created_at"):
            u.created_at = datetime.fromisoformat(data["created_at"])
        if data.get("updated_at"):
            u.updated_at = datetime.fromisoformat(data["updated_at"])
        return u
