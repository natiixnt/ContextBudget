# Cache

ContextBudget is local-first by default. The open-source build ships cache backend abstractions so the pack pipeline can stay stable as teams add stricter trust boundaries or future shared cache integrations.

## Built-in Backends

- `local_file`: default backend. Persists summary previews to `.contextbudget_cache.json` inside the repo.
- `shared_stub`: no-op shared/remote backend stub. It exercises the shared-cache interface without making network calls or persisting hosted state.
- `memory`: process-local backend intended for tests and advanced embedders.

## Local Cache

The default backend stores summary previews on disk and reuses them on later runs. This keeps OSS behavior local, deterministic, and inspectable.

```toml
[cache]
backend = "local_file"
summary_cache_enabled = true
cache_file = ".contextbudget_cache.json"
duplicate_hash_cache_enabled = true
```

`run.json` records cache details in a top-level `cache` block:

```json
{
  "cache": {
    "backend": "local_file",
    "enabled": true,
    "hits": 3,
    "misses": 1,
    "writes": 1
  },
  "cache_hits": 3
}
```

`cache_hits` remains as a compatibility field for existing consumers.

## Future Shared Cache Direction

`shared_stub` exists to make the cache boundary explicit today without shipping hosted infrastructure. It deliberately behaves as a deterministic miss-only backend:

- no network calls
- no hidden background sync
- no hosted service dependency
- no implicit trust expansion

Future team-level reuse can plug into the same backend interface under `contextbudget/cache/` without changing CLI contracts or the `run.json` artifact shape.

## Trust And Privacy

Cache entries are derived from repository contents. Treat them as sensitive code-adjacent data.

- Keep `local_file` when repository data must remain on the current machine.
- Only adopt a future shared backend if the cache operator is allowed to see the same repository content as the developers using it.
- Telemetry and cache are separate systems. Telemetry stays opt-in, disabled by default, and has no network sink in OSS.
- The shared-cache stub in OSS sends nothing anywhere.
