from __future__ import annotations

"""Entity models for the control plane multi-team analytics layer."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class Organization:
    """Top-level tenant that owns one or more projects."""

    id: int
    name: str
    slug: str
    created_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class Project:
    """A named project belonging to an organization."""

    id: int
    org_id: int
    name: str
    slug: str
    created_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class Repository:
    """A repository tracked within a project."""

    id: int
    project_id: int
    name: str
    path: str
    created_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "path": self.path,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class AgentRun:
    """A single agent context-packing run recorded against a repository."""

    id: int
    repo_id: int
    task: str
    token_usage: int
    tokens_saved: int
    context_size: int
    cache_hits: int
    created_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "repo_id": self.repo_id,
            "task": self.task,
            "token_usage": self.token_usage,
            "tokens_saved": self.tokens_saved,
            "context_size": self.context_size,
            "cache_hits": self.cache_hits,
            "created_at": self.created_at,
        }
