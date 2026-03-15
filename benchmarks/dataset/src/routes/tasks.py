from __future__ import annotations

"""Task CRUD route handlers (framework-agnostic request/response dicts)."""

from typing import Any, Optional

from src.models.task import TaskPriority, TaskStatus
from src.services.task_service import PermissionDeniedError, TaskNotFoundError, TaskService
from src.utils.helpers import build_error_response, build_success_response
from src.utils.validators import validate_pagination


class TaskRoutes:
    """Encapsulates task endpoint handlers; inject your WSGI/ASGI glue."""

    def __init__(self, service: Optional[TaskService] = None) -> None:
        self._svc = service or TaskService()

    # ------------------------------------------------------------------
    # POST /tasks
    # ------------------------------------------------------------------
    def create(self, body: dict, requester_id: str) -> tuple[dict, int]:
        title = body.get("title", "")
        description = body.get("description", "")
        priority_raw = body.get("priority", "medium")
        tags = body.get("tags", [])
        assignee_id = body.get("assignee_id")
        parent_id = body.get("parent_id")
        try:
            priority = TaskPriority(priority_raw)
        except ValueError:
            return build_error_response(f"Unknown priority: {priority_raw!r}"), 400
        try:
            task = self._svc.create_task(
                title=title,
                owner_id=requester_id,
                description=description,
                priority=priority,
                tags=tags,
                assignee_id=assignee_id,
                parent_id=parent_id,
            )
        except ValueError as exc:
            return build_error_response(str(exc)), 422
        return build_success_response(task.to_dict(), "Task created"), 201

    # ------------------------------------------------------------------
    # GET /tasks
    # ------------------------------------------------------------------
    def list_tasks(self, query: dict, requester_id: str) -> tuple[dict, int]:
        status_raw = query.get("status")
        priority_raw = query.get("priority")
        limit = int(query.get("limit", 50))
        offset = int(query.get("offset", 0))
        try:
            validate_pagination(limit, offset)
        except ValueError as exc:
            return build_error_response(str(exc)), 400
        status: Optional[TaskStatus] = None
        if status_raw:
            try:
                status = TaskStatus(status_raw)
            except ValueError:
                return build_error_response(f"Unknown status: {status_raw!r}"), 400
        priority: Optional[TaskPriority] = None
        if priority_raw:
            try:
                priority = TaskPriority(priority_raw)
            except ValueError:
                return build_error_response(f"Unknown priority: {priority_raw!r}"), 400
        tasks = self._svc.list_tasks(
            owner_id=requester_id, status=status, priority=priority, limit=limit, offset=offset
        )
        return build_success_response([t.to_dict() for t in tasks]), 200

    # ------------------------------------------------------------------
    # GET /tasks/:id
    # ------------------------------------------------------------------
    def get(self, task_id: str, requester_id: str) -> tuple[dict, int]:
        try:
            task = self._svc.get_task(task_id, requester_id=requester_id)
        except TaskNotFoundError:
            return build_error_response("Task not found", 404), 404
        except PermissionDeniedError:
            return build_error_response("Forbidden", 403), 403
        return build_success_response(task.to_dict()), 200

    # ------------------------------------------------------------------
    # PATCH /tasks/:id
    # ------------------------------------------------------------------
    def update(self, task_id: str, body: dict, requester_id: str) -> tuple[dict, int]:
        priority: Optional[TaskPriority] = None
        if "priority" in body:
            try:
                priority = TaskPriority(body["priority"])
            except ValueError:
                return build_error_response(f"Unknown priority: {body['priority']!r}"), 400
        try:
            task = self._svc.update_task(
                task_id,
                requester_id=requester_id,
                title=body.get("title"),
                description=body.get("description"),
                priority=priority,
                tags=body.get("tags"),
                assignee_id=body.get("assignee_id"),
            )
        except TaskNotFoundError:
            return build_error_response("Task not found", 404), 404
        except PermissionDeniedError:
            return build_error_response("Forbidden", 403), 403
        except ValueError as exc:
            return build_error_response(str(exc)), 422
        return build_success_response(task.to_dict()), 200

    # ------------------------------------------------------------------
    # POST /tasks/:id/complete
    # ------------------------------------------------------------------
    def complete(self, task_id: str, requester_id: str) -> tuple[dict, int]:
        try:
            task = self._svc.complete_task(task_id, requester_id=requester_id)
        except TaskNotFoundError:
            return build_error_response("Task not found", 404), 404
        except PermissionDeniedError:
            return build_error_response("Forbidden", 403), 403
        return build_success_response(task.to_dict()), 200

    # ------------------------------------------------------------------
    # DELETE /tasks/:id
    # ------------------------------------------------------------------
    def delete(self, task_id: str, requester_id: str) -> tuple[dict, int]:
        try:
            self._svc.delete_task(task_id, requester_id=requester_id)
        except TaskNotFoundError:
            return build_error_response("Task not found", 404), 404
        except PermissionDeniedError:
            return build_error_response("Forbidden", 403), 403
        return build_success_response(None, "Deleted"), 200

    # ------------------------------------------------------------------
    # GET /tasks/summary
    # ------------------------------------------------------------------
    def summary(self, requester_id: str) -> tuple[dict, int]:
        data = self._svc.get_task_summary(requester_id)
        return build_success_response(data), 200
