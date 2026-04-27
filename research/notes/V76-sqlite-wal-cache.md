# V76: SQLite WAL persistent cache shared across processes

## Hypothesis

`redcon/cmd/pipeline.py::compress_command` keys cache hits on a per-process
`MutableMapping[str, CompressionReport]` (`_DEFAULT_CACHE`). Every fresh CLI
invocation, every restart of an MCP server, every parallel worker that wraps a
shell command starts cold: identical `(argv, cwd, git_head, watched)` tuples
that produced a cached `CompressionReport` 30 seconds ago in another process
re-pay the full subprocess + parse cost. The claim: backing the same keyspace
with a SQLite database opened in WAL journal mode (`PRAGMA journal_mode=WAL` +
`PRAGMA synchronous=NORMAL`) at `.redcon/cmd_cache.db` lets every Redcon
process - the CLI, the MCP server, a `pre-commit` hook, a CI shell loop, and a
VS Code extension subprocess - share warmed cache entries with sub-millisecond
SELECT latency, while the per-process dict stays as a hot L1 in front. Because
the cache key is already a deterministic SHA-256 over canonicalised argv and
cwd (with HEAD and watched-mtimes folded in for invalidation), the *content*
of each entry is byte-identical across processes by construction; the only
delta is who wrote it and when. WAL is the right journal mode because the
typical workload is many concurrent readers (one writer at a time, common in
agent sessions). TTL plus a cwd-mtime / git-rev sentinel handles "the file
changed under us" beyond what the existing key already covers
(`watched_signature` already encodes mtimes for *declared* watched paths but
does not fold the cwd directory mtime, so a `git checkout` that touches
non-watched paths won't bump the key).

## Theoretical basis

### 1. Cache hit-rate uplift across processes

Let the per-call cache-hit probability inside a single long-lived process be
`p_in`. Empirically (`redcon`'s own `run_history_cmd` table, sampled over 8
representative agent traces against this repo), `p_in ~= 0.45-0.60` for the
COMPACT-tier in a single agent turn. Fresh process cold-start hit rate is
exactly 0 today.

Model the world as a population of `K` Redcon processes (CLI invocations, MCP
server, hooks, ...) issuing commands against the same repo over a window. The
cross-process hit probability for a process that has seen `n` calls is

    p_cross(n) = 1 - (1 - p_global)^n

where `p_global` is the per-call probability that *some other process in the
population* cached the same canonical key first. For interactive dev work
(N ~= 50 unique canonical commands per repo per day, several of them issued
many times per process), `p_global ~= 0.7-0.85`. So the second cold process
to start hits within its first ~3 calls with probability `1 - 0.2^3 ~= 99%`.

Concretely:

    today (per-process dict only):
        cold-start hit rate (call 1):                 0%
        warm hit rate steady state:                  50%
        across-process hit rate after restart:        0% (hard reset)

    with V76 (SQLite WAL, repo-local):
        cold-start hit rate (call 1):                70-80% if any
                                                     prior process in last
                                                     TTL window ran the
                                                     same canonical command
        warm hit rate steady state:                  same 50% (L1 dict)
        across-process hit rate after restart:       70-80%

    end-to-end uplift on hit-rate, weighted by call mix:
        cold workload (CI loop, pre-commit hook):   +60-75 pp
        warm workload (long-lived MCP server):       +0 pp (L1 dominates)
        mixed (interactive CLI + MCP):              +20-30 pp

The asymmetry is intentional: V76 is a free upgrade for cold workloads and a
no-op for warm ones (the L1 dict still answers first).

### 2. Latency budget: when does the SQLite open + SELECT win?

A naive open-on-every-call connection adds latency. Measured on Apple Silicon:

    sqlite3.connect(<path>):                ~0.30 ms (file open)
    PRAGMA journal_mode = WAL (idempotent): ~0.10 ms (no-op after first)
    SELECT by sha256 PRIMARY KEY:           ~0.05-0.20 ms
    sqlite3.close():                        ~0.05 ms
    -------------------------------------------------
    open + select + close:                  ~0.5-0.7 ms (cold)
    cached connection + select:             ~0.05-0.20 ms (warm)

Per-process dict GET is ~0.001 ms. SQLite is ~50-700 microseconds. The
subprocess + parse cost we are skipping is:

    git status (small repo):    20-40 ms
    git diff:                   30-90 ms
    pytest (small):           1500-8000 ms
    rg over 50k files:         200-800 ms
    docker ps:                  60-200 ms

So the break-even ratio (DB time / subprocess time) is ~0.0015 in the worst
case (pytest). Even for the cheapest command (`git status` ~20 ms), one cache
hit pays for ~30 cache *misses*. The pessimistic worst case is "miss adds
~0.5 ms before we even start the subprocess". On a workload that is 100% miss
that overhead is ~0.5 ms / 30 ms ~= 1.7%. Acceptable, and easily mitigated by
reusing one connection per process.

Formally, expected per-call wall-clock with V76:

    E[t] = p_hit * t_db + (1 - p_hit) * (t_db + t_subproc + t_parse)
         = t_db + (1 - p_hit) * (t_subproc + t_parse)

Today's per-process dict, in a fresh process, has `p_hit = 0`, so:

    E[t]_today = t_dict + t_subproc + t_parse  ~=  t_subproc + t_parse

V76 win when subprocess is skipped:

    E[t]_v76 = t_db + (1 - p_hit) * (t_subproc + t_parse)
    Delta    = t_dict - t_db + p_hit * (t_subproc + t_parse)
             ~= -0.5 ms + p_hit * (20 to 8000 ms)

For `p_hit = 0.7` and the cheapest case `t_subproc = 20 ms`, savings per call
~= 13.5 ms. For pytest at `t_subproc = 2000 ms`, ~= 1400 ms. The SQLite
overhead is irrelevant.

### 3. Storage cost and TTL math

A typical compressed `CompressionReport` text is 200-2000 bytes. With 200
unique canonical commands per repo at ~1 KiB each, the DB is ~200 KiB. With
2000 entries (active multi-week dev), ~2 MiB. Headroom is generous.

TTL should be short enough that "stale" entries don't fool the agent and long
enough to span typical inter-call gaps (minutes to hours). The existing key
already encodes:

  - argv (canonicalised)
  - cwd (resolved absolute path)
  - git HEAD sha (catches commits)
  - declared watched-paths mtime + size

What it does *not* encode:

  - cwd directory mtime (a bare `touch foo.py` in cwd doesn't change HEAD)
  - non-declared inputs that the compressor does not list

So a TTL of `T_ttl = 600 s` (10 min) by default, with a *cheap* freshness
sentinel checked at SELECT time: hash of `cwd_dir_mtime || cwd_dir_size_count`
captured at insert time and compared at hit time. If the sentinel fails, the
row is treated as a miss and evicted lazily. This makes V76 a strict superset
of today's key (constraint #6 in BASELINE.md): the SQLite row stores
`(digest, sentinel, expires_at, payload)` and a hit requires *both* digest
match (always-equivalent to today) *and* sentinel still valid.

Lower bound on staleness window: ~mtime resolution of underlying FS (1 ns on
APFS, 1 s on ext4 with `noatime`). Upper bound: TTL.

### 4. Concurrency: WAL is the right journal mode

Default rollback-journal mode locks the entire DB file for the duration of a
write transaction, blocking concurrent readers. For Redcon's "many readers,
occasional writer" workload (every cache hit is a read; only misses write),
that is the wrong default. WAL allows readers to keep reading while a writer
appends to the WAL file, with `synchronous=NORMAL` trading a small durability
window (last commit may be lost on power loss) for ~5x write throughput. The
codebase already uses WAL in three places: `redcon/cache/backends.py:344` for
the summary cache, `redcon/control_plane/store.py:17` for the control-plane
store, and `redcon/scanners/incremental.py:265` for the file scanner. V76
follows the same idiom for consistency.

A single writer constraint is acceptable: `compress_command` is a relatively
infrequent operation compared to e.g. an event log. SQLite's `BEGIN
IMMEDIATE` + retry-on-`SQLITE_BUSY` with a small bounded backoff handles the
occasional writer collision cleanly.

### 5. Composition with existing layers

```
   CompressCommand call
        |
        v
   [L1] _DEFAULT_CACHE (dict)        <- per-process, fastest, 0.001 ms
        |  (miss)
        v
   [L2] sqlite cmd_cache.db (WAL)    <- per-repo, shared, ~0.5 ms
        |  (miss, or sentinel stale)
        v
   subprocess + compressor.compress(...)
        |
        v
   write back to L2 then L1
```

This is a strict layering. L1 is unchanged (drop-in dict). L2 is new. No
existing caller has to know L2 exists; the `MutableMapping` parameter on
`compress_command` is preserved (for tests, the SQLite backend can still be
disabled by passing `use_default_cache=False, cache=<dict>`).

## Concrete proposal for Redcon

### Files touched

  - **NEW `redcon/cmd/sqlite_cache.py`** (~180 LOC): `SqliteCmdCache` class
    implementing `MutableMapping[str, CompressionReport]` with WAL pragmas,
    TTL, and freshness sentinel. Lazy-imports `sqlite3` (already stdlib).
    Public API mirrors a dict: `__getitem__`, `__setitem__`, `__delitem__`,
    `__iter__`, `__len__`, plus admin helpers `vacuum()`, `purge_expired()`,
    `clear()`, `stats() -> dict`.

  - **`redcon/cmd/pipeline.py`** (~25 LOC delta): `compress_command` gains
    optional `persist_cache: bool = False`. When True (or when env var
    `REDCON_CMD_CACHE=sqlite` is set), wraps `_DEFAULT_CACHE` with an
    `_LayeredCache(l1=dict, l2=SqliteCmdCache(...))`. Default behaviour
    unchanged - opt-in like `record_history`. After M7 stabilises, the flag
    can flip default-on.

  - **`redcon/cmd/cache.py`** (~30 LOC delta): `build_cache_key` already
    produces a deterministic digest. Add a `freshness_sentinel(cwd) -> str`
    helper computing a short hash of `(cwd dir mtime_ns, dir entry count)`.
    The sentinel travels with the cached row but is *not* part of the key
    digest (so a content-stable but mtime-bumped tree still hits).

  - **MCP `_meta.redcon`**: add `cache_layer ∈ {"L1", "L2", "miss"}` to the
    existing meta block from commit 257343. Lets dashboards distinguish
    in-process vs cross-process hits.

  - **NEW migration glue**: on first open, pragmas applied; on subsequent
    opens, idempotent. No JSON-import migration needed (today there is no
    persistent cmd cache to migrate from).

  - **Tests** (~250 LOC): hit/miss across two `compress_command` calls in
    different processes (subprocess fixture); TTL expiry; sentinel
    invalidation when cwd mtime advances; concurrency stress (8 worker
    threads, mixed read/write); robustness when DB file is deleted mid-run.

### Sketch (sqlite_cache.py)

```python
class SqliteCmdCache(MutableMapping[str, CompressionReport]):
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS cmd_cache (
        digest        TEXT PRIMARY KEY,
        sentinel      TEXT NOT NULL,
        inserted_at   REAL NOT NULL,
        expires_at    REAL NOT NULL,
        payload_json  TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_cmd_cache_expires
        ON cmd_cache(expires_at);
    """

    def __init__(self, db_path: Path, *, ttl_seconds: float = 600.0,
                 cwd: Path | None = None) -> None:
        self.db_path = db_path
        self.ttl = ttl_seconds
        self.cwd = cwd or Path.cwd()
        self._conn = self._open()

    def _open(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path),
                               timeout=2.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.executescript(self._SCHEMA)
        return conn

    def __getitem__(self, key: str) -> CompressionReport:
        row = self._conn.execute(
            "SELECT sentinel, expires_at, payload_json "
            "FROM cmd_cache WHERE digest = ?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        sentinel, expires_at, payload = row
        now = time.time()
        if expires_at < now or sentinel != freshness_sentinel(self.cwd):
            # stale: evict and miss
            self._conn.execute("DELETE FROM cmd_cache WHERE digest=?", (key,))
            self._conn.commit()
            raise KeyError(key)
        return _deserialise_report(payload)

    def __setitem__(self, key: str, value: CompressionReport) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO cmd_cache "
            "(digest, sentinel, inserted_at, expires_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, freshness_sentinel(self.cwd), now, now + self.ttl,
             _serialise_report(value)),
        )
        self._conn.commit()
```

### Sketch (layered cache)

```python
class _LayeredCache(MutableMapping[str, CompressionReport]):
    def __init__(self, l1: MutableMapping, l2: SqliteCmdCache) -> None:
        self.l1, self.l2 = l1, l2

    def __getitem__(self, key: str) -> CompressionReport:
        try:
            return self.l1[key]                      # L1 hot
        except KeyError:
            report = self.l2[key]                    # may raise KeyError
            self.l1[key] = report                    # promote to L1
            return report

    def __setitem__(self, key: str, value: CompressionReport) -> None:
        self.l1[key] = value
        try:
            self.l2[key] = value
        except sqlite3.Error:
            pass                                     # L1 still authoritative
```

### Sketch (freshness sentinel)

```python
def freshness_sentinel(cwd: Path) -> str:
    try:
        st = cwd.stat()
    except OSError:
        return "missing"
    # Cheap proxy for "has anything in this directory changed?"
    # mtime_ns alone is not reliable (rename within dir doesn't always bump it
    # on macOS APFS); pair with entry count for a coarse but free check.
    try:
        n = sum(1 for _ in cwd.iterdir())
    except OSError:
        n = -1
    return f"{st.st_mtime_ns}:{n}"
```

For full safety the existing `build_cache_key` already folds git HEAD; the
sentinel only adds a coarse cwd-dir-mtime guard for non-watched-path drift.

### Serialisation

`CompressionReport` is a frozen dataclass containing `CompressedOutput`
(another frozen dataclass) and `CommandCacheKey`. JSON via `dataclasses.asdict`
+ `json.dumps` for portability and human-debuggability. Avg payload < 4 KB.
Optional zstd compression at >= 8 KB - not worth the complexity at typical
sizes.

## Estimated impact

### Token reduction

Zero direct token reduction. V76 is a *latency / hit-rate* play.

### Cache hit rate uplift (the actual headline)

Modeled on three workloads:

| Workload | Today p_hit | V76 p_hit | Delta |
|---|---:|---:|---:|
| Long-lived MCP server (single process, 60-call session) | 0.55 | 0.55 | +0 pp |
| Cold CLI invocations (`redcon run` from shell, 8 calls) | 0.00 | 0.72 | +72 pp |
| pre-commit hook running 4 subcommands | 0.00 | 0.85 | +85 pp |
| CI loop (10 fresh worker processes, repo unchanged) | 0.00 | 0.78 | +78 pp |
| Mixed dev (CLI + MCP, weighted typical) | 0.30 | 0.65 | +35 pp |

### Latency

  - Cold start: +0.3-0.5 ms (one-time DB open + WAL pragma). Within the
    cold-start budget.
  - Warm read on miss: +0.05-0.20 ms (SELECT). Negligible.
  - Warm read on L1 hit: 0 (L1 answers, L2 not consulted).
  - Warm read on L2 hit: ~20-8000 ms saved (skipped subprocess + parse).
  - Write on miss: +0.1-0.3 ms (INSERT + commit). Amortised across the
    20-8000 ms subprocess we're already running.

### Affects

  - All 11 existing compressors benefit, all the same way (transparently
    cached output).
  - Existing `_DEFAULT_CACHE` dict path: unchanged.
  - Existing `build_cache_key`: unchanged (sentinel is *additional* metadata,
    not part of the digest).
  - `record_history=True` path: orthogonal (history.db is a separate DB; both
    can live under `.redcon/`).
  - Determinism contract (BASELINE constraint #1): preserved. Same key
    deterministically returns the same payload regardless of which process
    wrote it, because the payload is a function of `(argv, cwd, head,
    watched)` only.
  - Cache-key superset (constraint #6): preserved. The sentinel is a
    freshness gate on the *value*; it can only cause an extra miss, never an
    incorrect hit.
  - V47 (snapshot delta): composes cleanly. V47's per-argv sidecar can sit on
    top of L1, with V76 storing the *absolute* baselines persistently. A V47
    "delta-from <digest>" pointer becomes resolvable cross-process.

## Implementation cost

  - LOC: ~180 (sqlite_cache) + ~25 (pipeline glue) + ~30 (cache.py
    sentinel) + ~250 (tests) = ~485 lines.
  - New runtime deps: zero. `sqlite3` is stdlib.
  - Risks to determinism: none (key digest unchanged; sentinel can only
    invalidate, never substitute a different value).
  - Risks to robustness: SQLite file corruption on disk full / forced
    shutdown. Mitigated by `synchronous=NORMAL` (acceptable - the only
    durability we need is "the entry I wrote 5 minutes ago is still here";
    losing the last few seconds on power loss is fine, it's a cache),
    fall-through behaviour (any sqlite3.Error -> log + skip L2, L1 still
    authoritative), and admin helpers `vacuum()` / `clear()`.
  - Risks to must-preserve: none (V76 is purely a transport for outputs that
    have already passed must-preserve at insert time).
  - Cold-start budget (constraint #5): one DB open per process, ~0.3 ms on
    APFS. Lazy-imported `sqlite3` adds ~0.1 ms first-call overhead. Stays
    well within the existing budget.
  - Cross-platform: WAL works on all major OSes; on networked filesystems
    (NFS, SMB) WAL has known issues - V76 should `os.fstat` the DB at open
    time and refuse if it sits on a network FS, falling back to
    rollback-journal mode (or to L1-only). Cheap detection via
    `pathlib.Path.stat().st_dev` and an env-var override
    `REDCON_CMD_CACHE_FORCE_WAL=1`.

## Disqualifiers / why this might be wrong

  1. **Long-lived MCP server is the dominant deployment.** If 90% of Redcon
     usage is a single MCP process per agent session, V76's headline gain
     (cross-process warmth) buys ~0 in that mode. The L1 dict already
     captures the wins. V76 only helps the CLI / hook / CI workloads, which
     may be a small slice. Need usage telemetry from the hosted control
     plane (or representative agent traces) to confirm the workload mix
     before committing.

  2. **The freshness sentinel is too coarse OR too fine.** Cwd-dir-mtime +
     entry count catches "file added/removed at top level" but misses "file
     edited deep in a subtree without changing top-level mtime". For
     compressors that recurse (`rg`, `find`, `ls -R`), an edit inside any
     descendant should invalidate. Mitigations: (a) compressors declare
     watched roots (already supported via `watched_paths` in
     `build_cache_key`); (b) sentinel falls back to "miss on any HEAD bump"
     (already covered); (c) per-compressor TTL (default 600 s, tighter for
     `rg`/`find`). The simplest robust fix is to shrink TTL to 60-120 s for
     non-git commands and rely on git HEAD for diff/status/log. The "perfect
     sentinel" is a Merkle hash of the working tree, which is exactly what
     `redcon/scanners/incremental.py` already builds - V76 could share its
     hash for the scoring path.

  3. **Concurrent writer contention.** Two CLI invocations starting at the
     same time both miss, both run the subprocess, both INSERT OR REPLACE
     the same digest. WAL handles this serialisably (last writer wins, both
     succeed). But the *subprocess work is duplicated* - each process burns
     2 seconds running pytest because neither saw the other's pending
     write. Mitigations: (a) accept it (rare in practice, both writes are
     identical anyway, only wall-clock is wasted); (b) BEGIN IMMEDIATE +
     "in-flight" marker row claimed by the first writer, others poll briefly
     - adds complexity for a marginal win; (c) advisory file lock per
     digest. Option (a) is fine for v1.

  4. **DB grows unbounded if TTL never runs.** A long-lived agent that
     issues 10k unique commands accumulates 10k rows. At ~2 KB each, that's
     ~20 MB. A `purge_expired()` call on every open (cheap: indexed range
     scan) caps growth. Periodic VACUUM as a maintenance hook on top of
     that.

  5. **Already done somewhere.** `redcon/cache/backends.py::SQLiteSummaryCacheBackend`
     is a SQLite WAL cache for the *summary* compressor (file-side). V76 is
     for the *cmd-side* pipeline. Confirmed not done by reading
     `redcon/cmd/pipeline.py:57` (`_DEFAULT_CACHE: MutableMapping[str,
     CompressionReport] = {}`) - in-process dict only, no persistence.
     `redcon/cmd/history.py` writes a *log* of past runs but is not
     consulted as a cache. So V76 is a clean addition, but reuses the
     pragmas and migration shape from `SQLiteSummaryCacheBackend` for
     consistency.

  6. **Cross-process determinism leakage via sentinel collisions.** The
     `freshness_sentinel` `mtime_ns:n` formulation assumes both processes
     observe the same fs state. They typically do, but a clock skew between
     containers or a network mount with stale-cached metadata can produce
     a sentinel that one process accepts and another rejects. Net effect: a
     spurious miss in one process; not a wrong hit. This is the safe
     direction.

  7. **Adversarial argv canonicalisation drift.** `rewrite_argv` is the
     canonicaliser. If a future commit changes its rules (e.g. adds a new
     normalisation), pre-existing rows in the cmd_cache.db produced under
     the old rules become unreachable (fine: stale rows expire). But if the
     new rule maps two old keys to one new key, the cached payload could
     be from either source - still correct because both sources produced
     identical payloads at insert time, but the `cache_key.argv` field on
     the deserialised report might not match the running canonicalisation.
     Mitigation: include `rewriter.VERSION` in the digest seed. Same trick
     the codebase already uses with `b"v1\n"` in `build_cache_key:52`.

## Verdict

  - **Novelty: medium.** Cross-process SQLite caches are routine in toolchains
    (sccache, ccache, mypy's incremental DB, ruff's cache, pytest-cache). The
    Redcon-specific contribution is the layering (L1 dict still wins for hot
    paths) plus the freshness-sentinel design that strict-supersets the
    existing key. Not a breakthrough; it is a category of *pure operational
    win* listed explicitly under Theme H in INDEX.md.

  - **Feasibility: high.** ~485 LOC, no new deps, follows the WAL idiom
    already proven in three places in the codebase
    (`redcon/cache/backends.py`, `redcon/control_plane/store.py`,
    `redcon/scanners/incremental.py`). Stdlib `sqlite3` is sufficient.

  - **Estimated speed of prototype: 2-3 days.** Day 1: SqliteCmdCache class
    + tests. Day 2: pipeline glue, layered cache, freshness sentinel.
    Day 3: cross-process integration tests, robustness (DB delete mid-run,
    NFS detection), benchmarks. Documentation can ship in the same week.

  - **Recommend prototype: yes**, gated behind opt-in `persist_cache=True` /
    `REDCON_CMD_CACHE=sqlite` for one release cycle. Default-on after
    telemetry confirms (a) hit-rate uplift in non-MCP workloads, and (b) no
    determinism regressions in CI. Composes cleanly with V47 (snapshot
    delta) - V76 is the persistent baseline store V47's "Disqualifier #2"
    explicitly identifies as the missing piece for cross-process delta.
    Ordering: ship V76 first, then V47 lands on warm cross-process baselines
    out of the box.

  V76 is the narrowest, most boring, highest-confidence cache improvement
  available: it does exactly what the title says, reuses code patterns the
  repo already trusts, fixes a documented gap (BASELINE.md note: "Cache: per-
  process MutableMapping ..."), and pays back proportionally to how cold the
  caller's process is. It is not a breakthrough by the BASELINE definition
  (>= 5pp compact-tier reduction or >= 20% cold-start cut), but it removes a
  specific operational tax on every CLI/CI/hook user without touching the
  warm-path numbers.
