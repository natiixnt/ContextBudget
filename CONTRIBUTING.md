# Contributing

## Setup

```bash
python -m pip install -e .[dev]
pytest
```

## Guidelines

- Keep runtime dependencies minimal.
- Preserve deterministic behavior in core scoring/compression.
- Add tests for every behavior change.
- Avoid introducing model-provider coupling in core modules.

## Pull Requests

Include:
- problem statement
- approach and tradeoffs
- test coverage updates
- before/after sample report if behavior changes
