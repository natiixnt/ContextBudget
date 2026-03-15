# ContextBudget CLI — Docker image
# Provides the full contextbudget CLI and Python API.
# Use this image in CI/CD pipelines or as a base for custom integrations.
#
# Build:   docker build -t contextbudget .
# Run:     docker run --rm -v "$(pwd)":/repo contextbudget pack "my task" --repo /repo
#
# To use the gateway server:
#   docker run --rm -p 8787:8787 contextbudget gateway --host 0.0.0.0 --port 8787

FROM python:3.12-slim

LABEL org.opencontainers.image.title="ContextBudget CLI"
LABEL org.opencontainers.image.description="Optimize LLM context windows and enforce token budgets"
LABEL org.opencontainers.image.source="https://github.com/natiixnt/ContextBudget"

# Install system dependencies for git operations (used by drift/pr-audit commands)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install the package
COPY pyproject.toml ./
COPY contextbudget/ contextbudget/

RUN pip install --no-cache-dir -e .

# Default working directory for repository mounts
WORKDIR /repo

ENTRYPOINT ["python", "-m", "contextbudget.cli"]
CMD ["--help"]
