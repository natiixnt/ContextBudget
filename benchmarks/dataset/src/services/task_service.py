from __future__ import annotations

"""Task business logic layer."""

from typing import Optional

from src.db.repository import TaskRepository, UserRepository
from src.models.task import Task, TaskPriority, TaskStatus
from src.utils.validators import validate_task_title


class TaskNotFoundError(Exception):
    pass


class PermissionDeniedError(Exception):
    pass


class TaskService:
    def __init__(
        self,
        task_repo: Optional[TaskRepository] = None,
        user_repo: Optional[UserRepository] = None,
    ) -> None:
        self._tasks = task_repo or TaskRepository()
        self._users = user_repo or UserRepository()

    def create_task(
        self,
        title: str,
        owner_id: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        tags: Optional[list[str]] = None,
        assignee_id: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> Task:
        validate_task_title(title)
        task = Task(
            title=title,
            owner_id=owner_id,
            description=description,
            priority=priority,
            tags=tags or [],
            assignee_id=assignee_id,
            parent_id=parent_id,
        )
        return self._tasks.create(task)

    def get_task(self, task_id: str, requester_id: Optional[str] = None) -> Task:
        task = self._tasks.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if requester_id is not None and task.owner_id != requester_id:
            user = self._users.get_by_id(requester_id)
            if user is None or not user.is_admin():
                raise PermissionDeniedError("Not authorized to view this task")
        return task

    def list_tasks(
        self,
        owner_id: str,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        return self._tasks.list_by_owner(
            owner_id, status=status, priority=priority, limit=limit, offset=offset
        )

    def search_tasks(self, query: str, owner_id: Optional[str] = None) -> list[Task]:
        return self._tasks.search(query, owner_id=owner_id)

    def update_task(
        self,
        task_id: str,
        requester_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[TaskPriority] = None,
        tags: Optional[list[str]] = None,
        assignee_id: Optional[str] = None,
    ) -> Task:
        task = self.get_task(task_id, requester_id=requester_id)
        if title is not None:
            validate_task_title(title)
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority
        if tags is not None:
            task.tags = tags
        if assignee_id is not None:
            task.assign(assignee_id)
        return self._tasks.update(task)

    def complete_task(self, task_id: str, requester_id: str) -> Task:
        task = self.get_task(task_id, requester_id=requester_id)
        task.mark_done()
        return self._tasks.update(task)

    def archive_task(self, task_id: str, requester_id: str) -> Task:
        task = self.get_task(task_id, requester_id=requester_id)
        task.archive()
        return self._tasks.update(task)

    def delete_task(self, task_id: str, requester_id: str) -> bool:
        task = self.get_task(task_id, requester_id=requester_id)
        return self._tasks.delete(task.id)

    def get_task_summary(self, owner_id: str) -> dict:
        counts = self._tasks.count_by_status(owner_id)
        total = sum(counts.values())
        return {
            "total": total,
            "by_status": counts,
            "completion_rate": round(counts.get("done", 0) / total, 3) if total else 0.0,
        }

    def get_assigned_tasks(self, user_id: str) -> list[Task]:
        return self._tasks.list_by_assignee(user_id)
