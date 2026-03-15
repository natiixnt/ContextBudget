from __future__ import annotations

"""Tests for task service and routes."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.connection import configure as configure_db, initialize_schema
from src.services.task_service import TaskNotFoundError, TaskService
from src.models.task import TaskPriority, TaskStatus
from src.db.repository import UserRepository
from src.models.user import User


def _setup():
    configure_db(":memory:")
    initialize_schema()


def test_create_and_get_task():
    _setup()
    user_repo = UserRepository()
    owner = User(email="a@example.com", display_name="Alice")
    user_repo.create(owner)
    svc = TaskService()
    task = svc.create_task("Fix login bug", owner_id=owner.id, priority=TaskPriority.HIGH)
    assert task.id
    fetched = svc.get_task(task.id, requester_id=owner.id)
    assert fetched.title == "Fix login bug"
    assert fetched.priority == TaskPriority.HIGH


def test_complete_task():
    _setup()
    user_repo = UserRepository()
    owner = User(email="b@example.com", display_name="Bob")
    user_repo.create(owner)
    svc = TaskService()
    task = svc.create_task("Write tests", owner_id=owner.id)
    done = svc.complete_task(task.id, requester_id=owner.id)
    assert done.status == TaskStatus.DONE


def test_task_not_found():
    _setup()
    svc = TaskService()
    try:
        svc.get_task("nonexistent-id")
        assert False, "Should have raised"
    except TaskNotFoundError:
        pass


def test_list_tasks_with_filter():
    _setup()
    user_repo = UserRepository()
    owner = User(email="c@example.com", display_name="Carol")
    user_repo.create(owner)
    svc = TaskService()
    svc.create_task("Task A", owner_id=owner.id, priority=TaskPriority.HIGH)
    svc.create_task("Task B", owner_id=owner.id, priority=TaskPriority.LOW)
    high = svc.list_tasks(owner.id, priority=TaskPriority.HIGH)
    assert len(high) == 1
    assert high[0].title == "Task A"


def test_task_summary():
    _setup()
    user_repo = UserRepository()
    owner = User(email="d@example.com", display_name="Dave")
    user_repo.create(owner)
    svc = TaskService()
    t1 = svc.create_task("Task 1", owner_id=owner.id)
    svc.create_task("Task 2", owner_id=owner.id)
    svc.complete_task(t1.id, requester_id=owner.id)
    summary = svc.get_task_summary(owner.id)
    assert summary["total"] == 2
    assert summary["by_status"].get("done") == 1
