from __future__ import annotations

"""Data access layer for Task and User persistence."""

import json
from typing import Optional

from src.db.connection import get_connection
from src.models.task import Task, TaskStatus, TaskPriority
from src.models.user import User, UserRole


# ---------------------------------------------------------------------------
# User repository
# ---------------------------------------------------------------------------

class UserRepository:
    def create(self, user: User) -> User:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, display_name, role, is_active,
                                   password_hash, last_login_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.email,
                    user.display_name,
                    user.role.value,
                    1 if user.is_active else 0,
                    user._password_hash,
                    user.last_login_at.isoformat() if user.last_login_at else None,
                    user.created_at.isoformat(),
                    user.updated_at.isoformat(),
                ),
            )
        return user

    def get_by_id(self, user_id: str) -> Optional[User]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_email(self, email: str) -> Optional[User]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def list_all(self, active_only: bool = True) -> list[User]:
        with get_connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM users WHERE is_active = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY created_at DESC"
                ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def update(self, user: User) -> User:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE users SET display_name=?, role=?, is_active=?,
                    password_hash=?, last_login_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    user.display_name,
                    user.role.value,
                    1 if user.is_active else 0,
                    user._password_hash,
                    user.last_login_at.isoformat() if user.last_login_at else None,
                    user.updated_at.isoformat(),
                    user.id,
                ),
            )
        return user

    def delete(self, user_id: str) -> bool:
        with get_connection() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_user(row) -> User:
        return User.from_dict(
            {
                "id": row["id"],
                "email": row["email"],
                "display_name": row["display_name"],
                "role": row["role"],
                "is_active": bool(row["is_active"]),
                "password_hash": row["password_hash"],
                "last_login_at": row["last_login_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )


# ---------------------------------------------------------------------------
# Task repository
# ---------------------------------------------------------------------------

class TaskRepository:
    def create(self, task: Task) -> Task:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, title, description, owner_id, assignee_id,
                                   status, priority, tags, due_at, parent_id,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.description,
                    task.owner_id,
                    task.assignee_id,
                    task.status.value,
                    task.priority.value,
                    json.dumps(task.tags),
                    task.due_at.isoformat() if task.due_at else None,
                    task.parent_id,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )
        return task

    def get_by_id(self, task_id: str) -> Optional[Task]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_by_owner(
        self,
        owner_id: str,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        clauses = ["owner_id = ?"]
        params: list = [owner_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority.value)
        params.extend([limit, offset])
        sql = (
            "SELECT * FROM tasks WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def list_by_assignee(self, assignee_id: str) -> list[Task]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE assignee_id = ? ORDER BY priority DESC, created_at DESC",
                (assignee_id,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def search(self, query: str, owner_id: Optional[str] = None) -> list[Task]:
        pattern = f"%{query}%"
        if owner_id:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE owner_id = ? AND (title LIKE ? OR description LIKE ?) ORDER BY updated_at DESC",
                    (owner_id, pattern, pattern),
                ).fetchall()
        else:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE title LIKE ? OR description LIKE ? ORDER BY updated_at DESC",
                    (pattern, pattern),
                ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update(self, task: Task) -> Task:
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks SET title=?, description=?, assignee_id=?,
                    status=?, priority=?, tags=?, due_at=?, parent_id=?, updated_at=?
                WHERE id=?
                """,
                (
                    task.title,
                    task.description,
                    task.assignee_id,
                    task.status.value,
                    task.priority.value,
                    json.dumps(task.tags),
                    task.due_at.isoformat() if task.due_at else None,
                    task.parent_id,
                    task.updated_at.isoformat(),
                    task.id,
                ),
            )
        return task

    def delete(self, task_id: str) -> bool:
        with get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0

    def count_by_status(self, owner_id: str) -> dict[str, int]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks WHERE owner_id = ? GROUP BY status",
                (owner_id,),
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    @staticmethod
    def _row_to_task(row) -> Task:
        return Task.from_dict(
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "owner_id": row["owner_id"],
                "assignee_id": row["assignee_id"],
                "status": row["status"],
                "priority": row["priority"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "due_at": row["due_at"],
                "parent_id": row["parent_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
