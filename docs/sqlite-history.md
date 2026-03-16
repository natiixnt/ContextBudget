# SQLite Run History

Starting with v1.0-alpha, Redcon uses a SQLite database to store run history instead of a flat JSON file.

## Why SQLite

- **Indexed queries** - history lookups for scoring are O(log n) instead of full-file scans
- **Concurrent writes** - SQLite handles concurrent appends from parallel agents without corruption
- **No size limit** - avoids the memory and parse cost of loading large JSON arrays on every run
- **Inspectable** - any SQLite viewer works (`sqlite3 .redcon/history.db .tables`)

## Default paths

| File | Purpose |
|---|---|
| `.redcon/history.db` | SQLite history database (new default) |
| `.redcon/history.json` | Legacy JSON history (still read if SQLite unavailable) |
| `.redcon/history.json.migrated` | Renamed original after auto-migration |

## Auto-migration

On first access after upgrading to v1.0-alpha, Redcon automatically:

1. Detects the existing `history.json`
2. Imports all entries into `history.db`
3. Renames `history.json` to `history.json.migrated`

No manual steps are required. The migration is idempotent - if `history.db` already exists, the JSON file is not touched.

## Configuration

```toml
[cache]
run_history_enabled = true
history_db = ".redcon/history.db"   # SQLite path (new)
history_file = ".redcon/history.json"  # Legacy JSON path (still used for fallback)
history_max_entries = 200
```

To disable SQLite and revert to JSON:

```toml
[cache]
history_db = ""  # empty string disables SQLite
```

Or pass `use_sqlite=False` to the Python API:

```python
from redcon.cache import load_run_history

entries = load_run_history(repo_path, use_sqlite=False)
```

## Schema

```sql
CREATE TABLE run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    task TEXT NOT NULL,
    repo TEXT NOT NULL DEFAULT '',
    workspace TEXT NOT NULL DEFAULT '',
    selected_files TEXT NOT NULL DEFAULT '[]',
    ignored_files TEXT NOT NULL DEFAULT '[]',
    candidate_files TEXT NOT NULL DEFAULT '[]',
    token_usage TEXT NOT NULL DEFAULT '{}',
    result_artifacts TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_run_history_generated_at ON run_history(generated_at);
CREATE INDEX idx_run_history_repo ON run_history(repo);
```

List/array fields (`selected_files`, etc.) are stored as JSON strings.

## Querying directly

```bash
sqlite3 .redcon/history.db \
  "SELECT generated_at, task, json_array_length(selected_files) \
   FROM run_history ORDER BY generated_at DESC LIMIT 10;"
```
