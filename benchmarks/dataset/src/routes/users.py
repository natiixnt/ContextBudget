from __future__ import annotations

"""User route handlers."""

from typing import Optional

from src.models.user import UserRole
from src.services.user_service import (
    EmailAlreadyTakenError,
    InvalidCredentialsError,
    UserNotFoundError,
    UserService,
)
from src.utils.helpers import build_error_response, build_success_response


class UserRoutes:
    def __init__(self, service: Optional[UserService] = None) -> None:
        self._svc = service or UserService()

    # ------------------------------------------------------------------
    # POST /auth/register
    # ------------------------------------------------------------------
    def register(self, body: dict) -> tuple[dict, int]:
        email = body.get("email", "")
        display_name = body.get("display_name", "")
        password = body.get("password", "")
        role_raw = body.get("role", "member")
        try:
            role = UserRole(role_raw)
        except ValueError:
            return build_error_response(f"Unknown role: {role_raw!r}"), 400
        try:
            user = self._svc.register(email, display_name, password, role=role)
        except EmailAlreadyTakenError as exc:
            return build_error_response(str(exc)), 409
        except ValueError as exc:
            return build_error_response(str(exc)), 422
        return build_success_response(user.to_dict(), "Registered"), 201

    # ------------------------------------------------------------------
    # POST /auth/login
    # ------------------------------------------------------------------
    def login(self, body: dict) -> tuple[dict, int]:
        email = body.get("email", "")
        password = body.get("password", "")
        try:
            user = self._svc.login(email, password)
        except InvalidCredentialsError:
            return build_error_response("Invalid credentials", 401), 401
        return build_success_response({"user": user.to_dict(), "token": f"tok_{user.id}"}), 200

    # ------------------------------------------------------------------
    # GET /users
    # ------------------------------------------------------------------
    def list_users(self, query: dict, requester: dict) -> tuple[dict, int]:
        if requester.get("role") != "admin":
            return build_error_response("Admin only", 403), 403
        active_only = query.get("active_only", "true").lower() != "false"
        users = self._svc.list_users(active_only=active_only)
        return build_success_response([u.to_dict() for u in users]), 200

    # ------------------------------------------------------------------
    # GET /users/:id
    # ------------------------------------------------------------------
    def get(self, user_id: str, requester: dict) -> tuple[dict, int]:
        if requester.get("id") != user_id and requester.get("role") != "admin":
            return build_error_response("Forbidden", 403), 403
        try:
            user = self._svc.get(user_id)
        except UserNotFoundError:
            return build_error_response("User not found", 404), 404
        return build_success_response(user.to_dict()), 200

    # ------------------------------------------------------------------
    # PATCH /users/:id
    # ------------------------------------------------------------------
    def update(self, user_id: str, body: dict, requester: dict) -> tuple[dict, int]:
        if requester.get("id") != user_id and requester.get("role") != "admin":
            return build_error_response("Forbidden", 403), 403
        try:
            if "display_name" in body:
                user = self._svc.update_display_name(user_id, body["display_name"])
            else:
                user = self._svc.get(user_id)
        except UserNotFoundError:
            return build_error_response("User not found", 404), 404
        except ValueError as exc:
            return build_error_response(str(exc)), 422
        return build_success_response(user.to_dict()), 200

    # ------------------------------------------------------------------
    # DELETE /users/:id  (deactivate)
    # ------------------------------------------------------------------
    def deactivate(self, user_id: str, requester: dict) -> tuple[dict, int]:
        if requester.get("role") != "admin":
            return build_error_response("Admin only", 403), 403
        try:
            self._svc.deactivate(user_id)
        except UserNotFoundError:
            return build_error_response("User not found", 404), 404
        return build_success_response(None, "Deactivated"), 200
