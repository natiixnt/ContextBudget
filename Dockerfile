# Redcon CLI - Docker image
# Provides the full redcon CLI and Python API.
# Use this image in CI/CD pipelines or as a base for custom integrations.
#
# Build:   docker build -t redcon .
# Run:     docker run --rm -v "$(pwd)":/repo redcon pack "my task" --repo /repo
#
# To use the gateway server:
#   docker run --rm -p 8787:8787 redcon gateway --host 0.0.0.0 --port 8787

FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml ./
COPY redcon/ redcon/
RUN pip install --no-cache-dir .

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Redcon CLI"
LABEL org.opencontainers.image.description="Deterministic context budgeting for coding-agent workflows"
LABEL org.opencontainers.image.source="https://github.com/natiixnt/ContextBudget"

# Install git for drift/pr-audit commands
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/redcon* /usr/local/bin/

# Run as non-root user
RUN useradd --create-home --shell /bin/bash redcon
USER redcon

# Default working directory for repository mounts
WORKDIR /repo

ENTRYPOINT ["python", "-m", "redcon.cli"]
CMD ["--help"]
