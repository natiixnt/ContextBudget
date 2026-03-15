from __future__ import annotations

"""Application entry point and WSGI/ASGI bootstrap.

This module wires together the route handlers, database initialisation,
and middleware chain.  It intentionally avoids coupling to a specific web
framework so the benchmark dataset stays dependency-free.
"""

from src.config import load_config, AppConfig
from src.db.connection import configure as configure_db, initialize_schema
from src.routes.tasks import TaskRoutes
from src.routes.users import UserRoutes


class App:
    """Minimal WSGI-style application container."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()
        configure_db(self.config.db.url.replace("sqlite:///", "") if "sqlite" in self.config.db.url else ":memory:")
        initialize_schema()
        self.tasks = TaskRoutes()
        self.users = UserRoutes()

    def handle(self, method: str, path: str, body: dict, headers: dict) -> tuple[dict, int]:
        """Route an incoming request and return (response_body, status_code)."""
        requester_id: str = headers.get("X-User-Id", "")
        requester_role: str = headers.get("X-User-Role", "member")
        requester = {"id": requester_id, "role": requester_role}

        # Auth routes
        if method == "POST" and path == "/auth/register":
            return self.users.register(body)
        if method == "POST" and path == "/auth/login":
            return self.users.login(body)

        # User routes
        if method == "GET" and path == "/users":
            return self.users.list_users({}, requester)
        if method == "GET" and path.startswith("/users/"):
            uid = path.split("/")[2]
            return self.users.get(uid, requester)
        if method == "PATCH" and path.startswith("/users/"):
            uid = path.split("/")[2]
            return self.users.update(uid, body, requester)
        if method == "DELETE" and path.startswith("/users/"):
            uid = path.split("/")[2]
            return self.users.deactivate(uid, requester)

        # Task routes
        if method == "POST" and path == "/tasks":
            return self.tasks.create(body, requester_id)
        if method == "GET" and path == "/tasks":
            return self.tasks.list_tasks({}, requester_id)
        if method == "GET" and path == "/tasks/summary":
            return self.tasks.summary(requester_id)
        if method == "GET" and path.startswith("/tasks/"):
            task_id = path.split("/")[2]
            return self.tasks.get(task_id, requester_id)
        if method == "PATCH" and path.startswith("/tasks/"):
            task_id = path.split("/")[2]
            return self.tasks.update(task_id, body, requester_id)
        if method == "POST" and path.endswith("/complete"):
            task_id = path.split("/")[2]
            return self.tasks.complete(task_id, requester_id)
        if method == "DELETE" and path.startswith("/tasks/"):
            task_id = path.split("/")[2]
            return self.tasks.delete(task_id, requester_id)

        return {"error": "Not found", "status": 404}, 404


def create_app(config: AppConfig | None = None) -> App:
    return App(config=config)
