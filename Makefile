.PHONY: install dev test lint format check clean

install:
	python -m pip install -e .

dev:
	python -m pip install -e .[dev]
	pre-commit install

test:
	pytest --tb=short -q

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

check: lint test

clean:
	rm -rf build/ dist/ *.egg-info redcon.egg-info contextbudget.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
