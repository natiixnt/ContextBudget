from __future__ import annotations

"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class DatabaseConfig:
    url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///tasks.db"))
    pool_size: int = field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "5")))
    max_overflow: int = field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "10")))
    pool_timeout: int = field(default_factory=lambda: int(os.getenv("DB_POOL_TIMEOUT", "30")))
    echo: bool = field(default_factory=lambda: os.getenv("DB_ECHO", "false").lower() == "true")


@dataclass
class ServerConfig:
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    workers: int = field(default_factory=lambda: int(os.getenv("WORKERS", "1")))


@dataclass
class AppConfig:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", "dev-secret-change-in-prod"))
    allowed_origins: list[str] = field(
        default_factory=lambda: os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


def load_config() -> AppConfig:
    return AppConfig()
