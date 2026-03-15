# Benchmark Dataset - Task Manager API

A realistic multi-file Python service used as the target repository for
ContextBudget token-reduction benchmarks.

## Structure

```
src/
  app.py                 # Application entry point, request routing
  config.py              # Environment-driven configuration
  models/
    task.py              # Task domain model, status/priority enums
    user.py              # User domain model, password hashing
  db/
    connection.py        # SQLite connection management
    repository.py        # Data access layer (TaskRepository, UserRepository)
  services/
    task_service.py      # Task business logic
    user_service.py      # User/auth business logic
  routes/
    tasks.py             # Task CRUD endpoint handlers
    users.py             # User/auth endpoint handlers
  utils/
    validators.py        # Input validation
    helpers.py           # Response builders, pagination helpers
tests/
  test_tasks.py
  test_users.py
```

## Why this project?

The dataset is intentionally representative of a real-world service:

- **Multiple concerns** separated across files (models, services, DB, routes)
- **Cross-file dependencies** exercising the import-graph scorer
- **Keyword-rich names** (cache, auth, repository, connection) that
  match the benchmark tasks precisely
- **Realistic size** - large enough that naive full-context would be
  expensive, small enough that results are reproducible

## Benchmark tasks

| Task | Key files expected in context |
|------|-------------------------------|
| Add Redis caching to task lookup | `services/task_service.py`, `routes/tasks.py`, `db/repository.py` |
| Add JWT authentication middleware | `routes/users.py`, `services/user_service.py`, `models/user.py`, `app.py` |
| Refactor DB repository to connection pooling | `db/connection.py`, `db/repository.py`, `services/*.py` |
