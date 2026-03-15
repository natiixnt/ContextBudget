from __future__ import annotations

"""Task domain model and related value objects."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import uuid


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ARCHIVED = "archived"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Task:
    title: str
    owner_id: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    tags: list[str] = field(default_factory=list)
    due_at: Optional[datetime] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    assignee_id: Optional[str] = None
    parent_id: Optional[str] = None

    def mark_done(self) -> None:
        self.status = TaskStatus.DONE
        self.updated_at = datetime.now(timezone.utc)

    def archive(self) -> None:
        self.status = TaskStatus.ARCHIVED
        self.updated_at = datetime.now(timezone.utc)

    def assign(self, user_id: str) -> None:
        self.assignee_id = user_id
        self.updated_at = datetime.now(timezone.utc)

    def is_overdue(self) -> bool:
        if self.due_at is None:
            return False
        return datetime.now(timezone.utc) > self.due_at and self.status not in (
            TaskStatus.DONE,
            TaskStatus.ARCHIVED,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "owner_id": self.owner_id,
            "assignee_id": self.assignee_id,
            "status": self.status.value,
            "priority": self.priority.value,
            "tags": list(self.tags),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "parent_id": self.parent_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        due_at = None
        if data.get("due_at"):
            due_at = datetime.fromisoformat(data["due_at"])
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            title=data["title"],
            description=data.get("description", ""),
            owner_id=data["owner_id"],
            assignee_id=data.get("assignee_id"),
            status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
            priority=TaskPriority(data.get("priority", TaskPriority.MEDIUM.value)),
            tags=list(data.get("tags", [])),
            due_at=due_at,
            parent_id=data.get("parent_id"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
        )
