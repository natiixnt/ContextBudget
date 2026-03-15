from __future__ import annotations

"""Tests for user service."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.db.connection import configure as configure_db, initialize_schema
from src.services.user_service import (
    EmailAlreadyTakenError,
    InvalidCredentialsError,
    UserService,
)
from src.models.user import UserRole


def _setup():
    configure_db(":memory:")
    initialize_schema()


def test_register_and_login():
    _setup()
    svc = UserService()
    user = svc.register("alice@example.com", "Alice", "hunter2")
    assert user.id
    assert user.email == "alice@example.com"
    logged_in = svc.login("alice@example.com", "hunter2")
    assert logged_in.id == user.id
    assert logged_in.last_login_at is not None


def test_duplicate_email():
    _setup()
    svc = UserService()
    svc.register("bob@example.com", "Bob", "pass")
    try:
        svc.register("bob@example.com", "Bob2", "pass")
        assert False, "Should have raised"
    except EmailAlreadyTakenError:
        pass


def test_wrong_password():
    _setup()
    svc = UserService()
    svc.register("carol@example.com", "Carol", "right")
    try:
        svc.login("carol@example.com", "wrong")
        assert False, "Should have raised"
    except InvalidCredentialsError:
        pass


def test_deactivate_user():
    _setup()
    svc = UserService()
    user = svc.register("dave@example.com", "Dave", "pw")
    svc.deactivate(user.id)
    active_list = svc.list_users(active_only=True)
    assert all(u.id != user.id for u in active_list)


def test_change_role():
    _setup()
    svc = UserService()
    user = svc.register("eve@example.com", "Eve", "pw")
    assert user.role == UserRole.MEMBER
    updated = svc.change_role(user.id, UserRole.ADMIN)
    assert updated.role == UserRole.ADMIN
