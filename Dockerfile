# Redcon — Docker image
# Provides the full Redcon CLI, gateway server, and Python API.
# Use this image in CI/CD pipelines or as a base for custom integrations.
#
# Build:   docker build -t redcon .
# Run CLI: docker run --rm -v "$(pwd)":/repo redcon pack "my task" --repo /repo
#
# Gateway server (no Redis):
#   docker run --rm -p 8787:8787 redcon gateway --host 0.0.0.0 --port 8787
#
# Gateway server (with Redis session store):
#   RC_GATEWAY_REDIS_URL=redis://redis:6379/0 \
#   docker run --rm -p 8787:8787 redcon gateway --host 0.0.0.0 --port 8787
#
# See docker-compose.yml for the full gateway + Redis stack.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Redcon"
LABEL org.opencontainers.image.description="Deterministic context budgeting for coding-agent workflows"
LABEL org.opencontainers.image.source="https://github.com/natiixnt/ContextBudget"

# git is required for pr-audit and drift commands
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY redcon/ redcon/

# Install core package + gateway extras (FastAPI + uvicorn)
RUN pip install --no-cache-dir -e ".[gateway]"

# Default working directory for repository mounts
WORKDIR /repo

ENTRYPOINT ["python", "-m", "redcon.cli"]
CMD ["--help"]
