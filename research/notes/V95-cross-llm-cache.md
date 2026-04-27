# V95: Cross-LLM meta-cache - one compressed output reused across agents/models

## Hypothesis
Today's `redcon` cache is per-process: a `MutableMapping[str, CompressionReport]`
inside `redcon/cmd/pipeline.py`. Two agents (Claude + Cursor + Aider + a CI
linter bot) running on the same developer laptop, or N agents across a team
behind one git host, will each independently spawn `pytest`, `git status`,
`grep`, `lint`, `kubectl get pods`, etc. - and re-pay the parse + compression
cost N times for byte-identical output. A **shared cross-process / cross-host
cache** keyed on the existing deterministic `CommandCacheKey` lets the *first*
agent (or scheduled warmer) absorb the cost and every subsequent agent /
model / IDE / human in the org reuses the compressed result. The cache key is
already model-agnostic (no LLM identity in `build_cache_key`), so the
"cross-LLM" framing falls out for free: a Claude-driven `git status` and a
GPT-driven `git status` against the same `cwd + git HEAD + watched mtimes`
hash to the same digest. Prediction: in a multi-agent team setting, hit rate
on read-only commands (`git status`, `git log`, `ls`, `grep`, `kubectl get`,
`docker ps`, `lint`) climbs from "first call after spawn" (~0% today) to
60-90% steady state. Compression cost (parse + regex + token count) goes from
N to 1; agent latency on hits drops to a single round-trip. **Honest framing:
zero algorithmic novelty, this is product engineering on top of an existing
deterministic key. Mark as commercial differentiator, not research.**

## Theoretical basis
### Hit rate uplift, single-developer multi-agent
Let a developer run `K` agents concurrently in one workspace (Claude Code,
Cursor, an IDE plugin, a background test-watcher). Each agent independently
issues a sequence of read-only commands. The most invoked are
`git status`, `git diff`, `pytest`, `grep`, `lint`, `ls`, `find` (BASELINE
"11 compressors total"). Treat the per-agent call sequence as a Poisson
stream with rate `lambda` over a window where the underlying state (working
tree, git HEAD, watched mtimes) is unchanged for time `T`.

Per-process cache hit rate within an agent over `T`:
```
H_local(T)  =  1 - 1/N_T          where N_T = lambda * T
            ~= 1 - 1/(lambda T)
```
With shared cache across `K` agents:
```
H_shared(T) =  1 - 1/(K * lambda * T)
```
Marginal uplift `H_shared - H_local`. Concrete numbers, conservative:
- `lambda = 6 calls/min` per agent on a coding session (typical Claude Code).
- `T = 5 min` between meaningful tree changes (developer thinks, edits one
  file, runs tests).
- `K = 3` (Claude Code + Cursor sidekick + a test-watcher daemon).

Per agent: `N_T = 30`, `H_local = 0.967`. Sounds great - but only after the
30th call. The *first call per command per agent* is always a miss. The
cost-relevant metric is **misses-per-T**, not hit rate. Without sharing:
`K * U_T` misses where `U_T` is unique commands seen in window. With shared:
`U_T` misses, full stop. With `U_T = 7` (typical: status + diff + log +
pytest + grep + lint + ls), savings = `(K-1) * U_T = 14` parse+compression
operations every 5 minutes per developer. At ~50-300 ms per parse on
medium-large outputs that is 700 ms - 4.2 s of CPU per developer per
5 minutes - small but real, and crucially **deterministic latency floor on
"first call" hits drops to single-digit ms**.

### Hit rate uplift, multi-developer team
Now scale to a team of `D` developers on the same repo + same default
branch. Read-only commands like `lint <staged-files>` against `git HEAD ==
<merge-commit>` are *byte-identical* across every developer who has just
pulled. Same for `pytest -k smoke` on a tagged release, `grep -r TODO src/`
on `main` HEAD, `kubectl get pods -n staging` against the same cluster
state.

Define hit rate on a shared CI-/team-cache:
```
H_team  =  1 - 1 / (D * K * lambda * T_state)
```
where `T_state` is the time the *team's* shared upstream state is stable
(typical = minutes to hours for a release tag, seconds to minutes for
`main`). For `D = 20`, `K = 2`, `lambda = 4/min`, `T_state = 30 min`,
`N_T = 4800`, `H_team = 0.99979`. Equivalently: a single developer absorbs
the parse cost, the next 19 ride a remote hit. **The win is one OOM larger
than the single-developer case** because `T_state` for a tagged release is
much longer than `T` for a working tree, and `D` multiplies it.

### Network round-trip break-even
Shared cache imposes a network read on hit. Compute when remote-hit beats
local-miss-and-recompute:
```
break-even:  T_net + T_decode  <  T_subprocess + T_parse + T_compress
```
- `T_subprocess` (just spawning + producing bytes) for `git status`: ~30-80 ms.
- `T_parse + T_compress`: 5-50 ms small repos, up to several hundred for
  big diffs / 50 MB log-pointer cases.
- `T_net` (Redis on LAN, p50): 0.3-1 ms; (Redis WAN cross-region p50): 30-80 ms.
- `T_decode` (gzip + JSON): 1-5 ms.

LAN / colocated dev box: shared hit is ~10-100x cheaper than miss. WAN:
break-even tight; for trivial commands (`ls`) shared remote hit can be
*slower* than local re-execute. Mitigation: tier the cache (L1 in-process
+ L2 SQLite-WAL on local disk + L3 team Redis), and only consult L3 when
local L2 miss + command's expected parse cost exceeds `T_net`.

### Privacy / confidentiality bound
Compressed output may carry secrets:
- `git status` showing `.env.local` modified (path leaks repo layout).
- `grep -r AWS_KEY src/` may *be* the secret.
- `kubectl get secrets -o yaml` literally contains key material.
- `pytest` failure tracebacks may expose internal database hostnames.

Information-theoretic risk: shared cache widens the blast radius of any
single secret-bearing entry from one process to N readers. The cache key
itself can also leak: `argv = ("grep", "AKIA...", "src/")` is a hash
preimage worth brute-forcing if argv text is keyed verbatim.

Per-machine deterministic AES-256-GCM with a key derived from a workspace
secret (file or OS keychain) gives confidentiality at rest and on the wire
without requiring a centralized KMS. Cache key digest must be HMAC-keyed
(not raw SHA-256 of argv) to defeat preimage attacks on argv content.

## Concrete proposal for Redcon
Three tier-2 backends behind a uniform interface, all opt-in. Default path
remains the in-process map (BASELINE constraint #2: no required network).

### Backend abstraction
```python
# redcon/cmd/cache_backends.py (new ~300 LOC)
class CacheBackend(Protocol):
    def get(self, digest: str) -> CompressionReport | None: ...
    def put(self, digest: str, report: CompressionReport, ttl_s: int) -> None: ...
    def stats(self) -> dict: ...

# Implementations:
class InProcBackend(CacheBackend): ...           # current, default
class SqliteWalBackend(CacheBackend): ...        # composes V76, local disk
class RedisBackend(CacheBackend): ...            # team-shared, opt-in
class S3Backend(CacheBackend): ...               # cold tier, CI artifact
```

### Layered lookup
```python
# redcon/cmd/pipeline.py - replace single-cache lookup with chain
for backend in self._cache_chain:                # [L1, L2, L3]
    cached = backend.get(cache_key.digest)
    if cached is not None:
        if backend is not self._cache_chain[0]:  # promote upward
            self._cache_chain[0].put(cache_key.digest, cached, ttl_s=300)
        return _with_cache_hit(cached)
# miss: compute, write to L1+L2 always, write to L3 if budget says yes
```

### Privacy gate before remote write
```python
# redcon/cmd/cache_privacy.py (new ~200 LOC)
def safe_for_remote(report: CompressionReport, argv: tuple[str, ...]) -> bool:
    if any(p.match(arg) for p in _SECRET_ARGV_PATTERNS for arg in argv):
        return False                         # argv looks secret-bearing
    if report.detected_secrets:              # parser flagged AWS/GCP/JWT shapes
        return False
    if any(d in report.compressed for d in _DENYLIST_DOMAINS):
        return False                         # internal hostnames
    return True

# AES-GCM around the wire payload, key from $XDG_CONFIG_HOME/redcon/team.key
def seal(report) -> bytes: ...
def open_(blob) -> CompressionReport: ...
```

### Configuration
```toml
# redcon.toml
[cache]
backends = ["inproc", "sqlite", "redis"]
[cache.redis]
url = "redis://team-cache.internal:6379/0"
ttl_seconds = 1800
encryption = "aes-gcm"
key_path = "~/.config/redcon/team.key"
[cache.privacy]
deny_argv_patterns = ["AKIA[0-9A-Z]{16}", "ghp_[A-Za-z0-9]{36}"]
deny_output_domains = ["secrets.internal", "vault."]
remote_only_when_parse_cost_ms_above = 20
```

### Cache key hardening
Replace raw SHA-256 in `redcon/cmd/cache.py` with HMAC-SHA-256 keyed on a
per-workspace secret when remote backends are enabled. Local-only mode
keeps current SHA-256 (zero-config requirement preserved). The digest is
unchanged in shape (64 hex), so `_with_cache_hit` and `cache_key.short()`
are untouched.

### Concrete breakthrough definition
This proposal cannot move the **per-call** compact-tier reduction (the
output bytes are identical to today). But under BASELINE's clause "cuts
cold-start latency by >=20%" applied to the *org-wide aggregate* of all
redcon-mediated agent calls, a 60-90% L3 hit rate on read-only team
commands is a 5-10x reduction in compute spent on parsing. That is a
deployment-shape breakthrough, not an algorithmic one.

## Estimated impact
- Token reduction: **0**. Output bytes byte-identical to current path.
  Cross-LLM meta-cache changes nothing about *what* the agent sees; only
  *how often we recompute it*.
- Latency:
  - Single-dev multi-agent (`K=3`): **~700 ms - 4.2 s saved per developer
    per 5 min** of active session, mostly on `pytest`/`grep`/`lint`.
  - Multi-dev team (`D=20`, `K=2`): **>=99% L3 hit rate** on stable-HEAD
    commands. First call per (cmd, HEAD) per team pays full cost; rest are
    free network hits.
  - WAN-only deployment: degrades to neutral or slight regression on
    sub-20-ms commands; cost-gate avoids this.
- Affects:
  - `redcon/cmd/pipeline.py` - cache chain instead of single map.
  - `redcon/cmd/cache.py` - HMAC key option, otherwise unchanged.
  - `redcon/cache/` already has `run_history_sqlite.py` - composes here.
  - New `redcon/cmd/cache_backends.py`, `redcon/cmd/cache_privacy.py`.
  - MCP `_meta.redcon` block (BASELINE: "cache_hit") gains
    `cache_tier ∈ {inproc, sqlite, redis, miss}` so observability surfaces
    where the hit came from.
- Composes with:
  - **V76** (SQLite WAL): is exactly the L2 backend here. V76 makes cache
    cross-process on a single host; V95 generalises that to cross-host.
  - **V42** (hash-keyed shared dictionary at block grain): orthogonal -
    V42 dedups *within* a compressed payload; V95 dedups *the whole
    payload* across callers.
  - **V47** (snapshot delta): a remote hit on prior `(cmd, HEAD-1)` enables
    a delta-encoded response for `(cmd, HEAD)`, multiplying savings.

## Implementation cost
- Lines of code (rough):
  - Backend abstraction + InProc/Sqlite/Redis/S3 impls: ~600 LOC.
  - Privacy gate + denylists + AES-GCM seal/open: ~250 LOC.
  - HMAC keyed-cache option in `cache.py`: ~40 LOC + migration shim.
  - Config plumbing in `redcon.toml`: ~100 LOC.
  - Tests (golden + denylist + fault-injection on Redis down): ~400 LOC.
  - Total: **~1400 LOC** + ops/runbook docs.
- New runtime deps:
  - `redis` (Python) optional, lazy-imported. **Optional** -> does not break
    BASELINE constraint #2 ("no required network"); default path is
    in-process map untouched.
  - `cryptography` for AES-GCM (already widely shipped).
  - No embeddings, no model calls -> #3 preserved.
- Risks to determinism (#1): cache hit must return byte-identical
  `CompressionReport` (modulo `cache_hit=True` flag and `cache_tier`
  field). Serialisation round-trip must be lossless. Solvable with strict
  schema-versioned msgpack/JSON.
- Risks to robustness (#4 must-preserve): zero - we are not transforming
  output, only relocating storage. Quality harness in
  `redcon/cmd/quality.py` runs unchanged.
- Risks to cold-start (#5 ~62% lazy-import savings): backend constructors
  must be lazy. Redis connection on first L3 access, not on import.
- Risks to cache-key determinism (#6: "must be a strict superset"): when
  HMAC mode enabled, keys diverge from raw-SHA mode -> mark as namespaced
  (prefix `v2h:` vs current `v1`), do not collide across modes.

## Disqualifiers / why this might be wrong
1. **Almost no novelty.** This is "put a Redis in front of a deterministic
   pure function". Build systems (Bazel remote cache, sccache, ccache,
   Gradle build cache, Turborepo remote cache) have shipped this exact
   pattern for a decade. The contribution here is purely that the function
   in question is `compress_command(argv, cwd, HEAD)` rather than `compile`
   or `link`. Mark **Novelty: low**. The vector exists in the index
   (E. Cross-call dictionary) only as a productisation slot.
2. **Hit rate may be much lower in practice.** The cache key includes git
   HEAD + watched mtimes. Active development *constantly* invalidates
   HEAD (each commit) and tree mtimes (each save). The high `H_team`
   number assumes commands run against `main` HEAD that everyone has just
   pulled. Real working-tree commands (`git diff`, `pytest` against
   modified code) have `D = 1` effectively because each developer's tree
   is unique. **The win concentrates on a specific subset:** read-only
   queries against shared upstream state (CI pipelines, post-merge
   commands, kubectl/docker against shared infra, lint on tagged
   releases). For the laptop-only single-dev case the gain is small.
3. **Privacy is the dealbreaker for adoption, not the technology.**
   Enterprises will be hostile to a service that materialises potentially
   secret command outputs in a shared store. AES-GCM + denylists are
   table stakes; the real friction is compliance review, audit logs,
   key rotation, GDPR data-subject-erasure on cache entries that mention
   user names/emails (`git log`!), region pinning. None of that is hard
   technically; all of it is bureaucratically expensive to deploy. A
   one-developer-per-Redis-instance fallback may be the only viable
   default.
4. **Cache poisoning surface.** Anyone with write access to the shared
   Redis can plant a malicious "compressed" `pytest` output saying "all
   tests passed" with a valid HMAC of an attacker-known key. Threat model
   must assume the cache is mutually trusted; org-level deployment needs
   write-AuthN/AuthZ, signed entries, or read-only-from-CI write-by-CI
   architecture. Without that, the cache becomes a supply-chain attack
   vector that the agent will dutifully read and act on.
5. **WAN latency murders the win on small commands.** A 50 ms RTT to a
   remote Redis dwarfs a 30 ms `git status`. The cost-gate
   (`remote_only_when_parse_cost_ms_above`) is correct but means the
   shared cache *only matters for the slow commands*. Fortunately those
   are exactly the commands where the win is meaningful (`pytest` parse
   on 100 MB of TAP output, kubectl decompression, docker log-pointer
   tier).
6. **Already half-done by V76.** A SQLite WAL cache shared by all
   processes on one host already captures the single-laptop-multi-agent
   slice (the realistic 80% case). Going beyond that to team-Redis
   captures the remaining ~20% but pays the privacy/ops cost. Many
   adopters will stop at V76. **V95 is V76-with-network-tax.**
7. **Determinism contract subtly stretched.** Today, BASELINE requires
   "deterministic same-input-same-output". A remote backend can return a
   stale entry whose key matches but whose ground truth has rotated
   (e.g. `kubectl get pods` produces same argv+cwd+HEAD but cluster state
   differs). The cache key must be widened to include a refresh signal
   (kubeconfig context + namespace + a short TTL) for cluster-state
   commands. This is a per-compressor schema change in the registry, not
   a single-line fix.

## Verdict
- Novelty: **low**. (Productisation, not algorithm. Bazel remote cache
  pattern applied to a different pure function. Flag as
  **commercial differentiator** for enterprise tier - small dev teams
  won't pay for it; orgs with >=50 developers + CI fleet absolutely will.)
- Feasibility: **high**. The cache key is already deterministic and
  model-agnostic (`build_cache_key` reads no model identity), so the
  meta-cache property is automatic. Ops complexity is the only real
  cost.
- Estimated speed of prototype: **~2 weeks** for L1+L2+L3 + privacy gate
  + tests; **+2 weeks** for production-grade (signed entries, key
  rotation, audit log, k8s deployment chart).
- Recommend prototype: **conditional-on-X** where X = "Redcon is
  positioning toward enterprise/team-tier monetisation". As pure
  research, **no** - V76 captures most of the same win locally with
  ~10% of the surface area. As a commercial feature shipping behind a
  flag with V76 already in place, **yes** - the dev-experience story
  ("install team-cache, every CI parse becomes free for every laptop")
  is concrete and demonstrable, and the underlying determinism
  guarantees of redcon (same argv + same HEAD + same mtimes -> same
  bytes) are exactly what makes a sound shared cache *possible* where
  most CLI tools cannot offer one.
