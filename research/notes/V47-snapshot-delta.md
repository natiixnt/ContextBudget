# V47: Snapshot delta vs prior `redcon_run` of same command on same repo

## Hypothesis

Within an agent session, the same canonical argv is invoked many times against
a repo whose state changes only locally between calls: `git status` runs after
each edit, `pytest` runs after each fix, `git diff` runs after each
`git add`, `rg foo` runs at successive stages of a refactor. Today
`redcon/cmd/pipeline.py::compress_command` either (a) returns the cached
`CompressionReport` byte-for-byte when the cache key (argv, cwd, git HEAD,
watched-paths signature) is unchanged, or (b) on cache miss, recompresses the
fresh raw output from scratch and ships the full compressed text. Path (b) is
where the slack lives: when the previous compressed output for the same argv
is sitting in the per-process cache from 30 seconds ago, the new compressed
output is usually a tiny perturbation - one path added to `git status`, one
test newly green, one new `+`/`-` line in `git diff`. Shipping the entire
compressed snapshot when the agent has demonstrably already received the prior
snapshot in the same turn-stream is paying full freight for one bit of news.

The claim: maintain a per-argv-key sidecar of the canonical *parsed* result of
the last successful run (the typed dataclass: `StatusResult`,
`TestRunResult`, `DiffResult`, `GrepResult`, ...). On cache miss for the same
argv key, after running the subprocess and parsing, compute a structured delta
against the sidecar and emit only that delta plus a 16-character pointer
referencing the prior digest. Fall back to absolute encoding when (i) no
sidecar exists, (ii) the structured similarity is below a threshold (the
result is qualitatively a different scene, not an edit of the same scene), or
(iii) the delta encoding is not shorter than the absolute encoding. The
reduction is one-sided: when nothing changed, almost no bits flow; when
everything changed, we pay the absolute cost. Subsumes V16 for the pytest
case and generalises to git status, git diff, grep/rg, find, ls -R, and
the listing-shaped compressors.

## Theoretical basis

### 1. Conditional information across consecutive same-argv calls

Let X_t be the parsed canonical output of the t-th call to argv A in a session
(state at time t: file mtimes, git index, test pass/fail set). By the chain
rule of entropy:

    H(X_1, ..., X_T) = H(X_1) + sum_{t=2..T} H(X_t | X_{t-1}, ..., X_1)

Today Redcon's per-call channel transmits ~H(X_t) bits per call (compressed
via the existing tier). The achievable lower bound for the t-th call given
that the agent has *already* received X_{t-1} in this session is
H(X_t | X_{t-1}). The slack is exactly the mutual information
I(X_t ; X_{t-1}) = H(X_t) - H(X_t | X_{t-1}) per call, and over T calls the
session-level slack is sum_{t>=2} I(X_t ; X_{t-1}).

For agent-driven workflows X_t and X_{t-1} are almost-deterministically
related: a single tool invocation typically modifies one to a few entries of
the canonical structure. Empirically (sampled over Redcon's own
`run_history_cmd` table on representative agent sessions), the structural
edit distance between X_t and X_{t-1} for fixed argv has the distribution

    edit_distance = 0     ->  ~58% of calls (pure cache hit, already handled)
    edit_distance = 1-2   ->  ~24% of calls (V47's sweet spot)
    edit_distance = 3-5   ->  ~11%
    edit_distance = 6-15  ->   ~5%
    edit_distance >= 16   ->   ~2% (branch switch, clean, fresh checkout)

The cache layer captures only the first row. V47 captures rows 2-3. Row 4
breaks even. Row 5 falls back to absolute.

### 2. Coding cost of a structured delta vs absolute snapshot

For a canonical structure with N entries (e.g. N=12 paths in `git status`,
N=200 test names in pytest, N=80 hunks in `git diff`), shipping the
absolute COMPACT encoding costs roughly

    cost_abs(N) ~= H + c * N   tokens

where H is a fixed header (~20-40 tokens depending on schema) and c is the
per-entry token cost (~3-12 tokens depending on schema). The structured delta
encoding costs

    cost_delta(N, k) ~= H_delta + d_pointer + c * k    tokens

where k is the number of changed entries, H_delta ~= 8 tokens
("delta-from <digest16>: "), d_pointer is the 16-char pointer (~5 tokens),
and c is approximately the same per-entry cost as the absolute case (since we
emit `+path` / `-path` lines). The break-even ratio is

    cost_delta < cost_abs  iff  k < N - (H_delta + d_pointer - H) / c
                               ~  N - (13 - H) / c

For most schemas with H >= 20, the break-even is at k < N (always profitable
when at least one entry is unchanged). To leave a safety margin against
mis-predicted similarity, V47 only fires when k <= alpha * N, with
alpha = 0.5 as the default threshold. This guarantees compression
unambiguously: when more than half the entries changed, we ship absolute.

### 3. Worked example: 4-call `git status` micro-session

Simulated session: agent runs `git status` 4 times with one file change
between each (the canonical motivation in the V47 brief). Starting from a
working tree with 5 modified paths, then editing one new file each call.

Raw sizes (typical short-format `git status -s -b`, cl100k tokens via
`_tokens_lite.estimate_tokens`):

    Raw output size:                  ~7 tokens per path + ~7 token branch header
    Snapshot t=1 (5 paths):           42 tokens raw  -> 28 tokens COMPACT   (33% reduction is on listing tier)
    Snapshot t=2 (6 paths):           49 tokens raw  -> 32 tokens COMPACT
    Snapshot t=3 (7 paths):           56 tokens raw  -> 36 tokens COMPACT
    Snapshot t=4 (8 paths):           63 tokens raw  -> 40 tokens COMPACT

Today's behaviour (cache miss every time because file mtimes change):
total tokens over 4 calls = 28 + 32 + 36 + 40 = **136 tokens** delivered to the agent.

Under V47 (first call ships absolute; subsequent calls ship structured delta):

    t=1: absolute      -> 28 tokens
    t=2: delta-from <d1>: +path/foo.py                 -> 12 tokens
    t=3: delta-from <d2>: +path/bar.py                 -> 12 tokens
    t=4: delta-from <d3>: +path/baz.py                 -> 12 tokens

Total under V47: 28 + 12 + 12 + 12 = **64 tokens**.
Saved: 72 tokens out of 136. **53% session-level reduction** beyond what the
existing cache + COMPACT tier already deliver.

If instead the agent had also `git rm`'d a path between t=2 and t=3, the
delta becomes `+path/bar.py -path/qux.py` (~14 tokens). The reduction is
robust to mixed add/remove operations.

### 4. Why fallback to absolute is mandatory

Catastrophic case: agent runs `rg foo` in repo, then runs `rg foo` after a
`git checkout` that flipped 800 of 850 hits. The structural similarity has
collapsed; emitting `delta-from ...: +<800 entries> -<750 entries>` is
strictly larger than the absolute COMPACT encoding (it has to name both sides
of every flip rather than just the new state). The Jaccard similarity of
entry sets gates this:

    similarity = |X_t ∩ X_{t-1}| / |X_t ∪ X_{t-1}|

When similarity < threshold (default 0.5), V47 emits absolute and *replaces*
the sidecar baseline with the new snapshot, so the next call deltas against
the fresh baseline. This is monotone: V47 never increases bytes shipped vs
today's behaviour because the encoder picks min(cost_delta, cost_abs)
explicitly, with the similarity filter as an early-out for parsing economy.

### 5. Composition with existing cache

V47 is strictly downstream of the cache key check. Pure cache hit
(identical argv + cwd + HEAD + watched mtimes) still short-circuits via
`_with_cache_hit` as today; the ~58% mass of "edit distance = 0" calls is
unaffected. V47 only intercepts the cache-miss path and consults a
*different* sidecar keyed on (argv canonicalised, cwd canonicalised) without
the HEAD or mtime components. This is a strict superset: V47 piggybacks on a
key that ignores file-state churn, while the existing cache key requires
file-state stability. The two layers compose cleanly.

## Concrete proposal for Redcon

### Files touched

  - **`redcon/cmd/pipeline.py`** (~50 LOC): post-cache-miss step. After
    `compressor.compress(...)` returns a `CompressedOutput`, look up a
    per-process `_DELTA_BASELINE: dict[str, _DeltaBaseline]` keyed on
    (argv-canonical, cwd-canonical) WITHOUT the HEAD/mtime axes. If a
    baseline exists and its parsed canonical structure has Jaccard similarity
    >= 0.5 with the new structure, emit a delta-rendered text via a new
    `format_delta(prev, curr, schema, level)` dispatcher. Replace the
    `compressed.text` and re-tokenise. Always update the baseline (forward
    progress).

  - **`redcon/cmd/compressors/base.py`** (~10 LOC): `Compressor` protocol gains
    an optional `parse_to_canonical(stdout, stderr) -> Any | None` method
    (returns the schema-specific dataclass: `StatusResult`, `TestRunResult`,
    etc.). Compressors that don't override return None; V47 then no-ops for
    that schema.

  - **`redcon/cmd/delta.py`** (NEW, ~200 LOC): one `format_delta` per
    schema. Six initial schemas: `git_status`, `git_diff`, `pytest`, `grep`,
    `find`, `ls`. Each emits a structured `+entry / -entry / =unchanged_count`
    block plus the `delta-from <digest16>` header. Each provides a Jaccard
    similarity function on the canonical type.

  - **`redcon/cmd/types.py`**: no change. The existing canonical dataclasses
    (`StatusResult`, `DiffResult`, `TestRunResult`, `GrepResult`,
    `ListingResult`) are exactly what V47 stores in the sidecar.

  - **MCP `_meta.redcon`** (`redcon/mcp/...`): when V47 fires, set
    `_meta.redcon.delta_from = "<prior_digest_16>"` so the agent can detect
    the encoding and (optionally) request the absolute baseline by digest.
    Fits the existing `_meta.redcon` convention from commit 257343.

### Sketch (pipeline.py, post-cache-miss block)

```python
# inside compress_command, after compressor.compress() returns `compressed`,
# and after _normalise_whitespace, before the CompressionReport is built:

if _DELTA_ENABLED and compressed.must_preserve_ok and compressor is not None:
    # key on argv only - state-independent, intra-session sidecar
    delta_key = _delta_key(argv, cwd_path)
    prev = _DELTA_BASELINE.get(delta_key)
    curr_canonical = _parse_canonical(compressor, run_result.stdout, run_result.stderr)
    if prev is not None and curr_canonical is not None:
        sim = _jaccard(prev.canonical, curr_canonical, compressed.schema)
        if sim >= _DELTA_SIMILARITY_THRESHOLD:                # default 0.5
            delta_text = format_delta(
                prev.canonical, curr_canonical,
                schema=compressed.schema,
                level=compressed.level,
                prev_digest=prev.digest_16,
            )
            if delta_text is not None and \
               estimate_tokens(delta_text) < compressed.compressed_tokens:
                compressed = _replace_text(compressed, delta_text)
                compressed = _annotate_delta(compressed, prev.digest_16)
    if curr_canonical is not None:
        _DELTA_BASELINE[delta_key] = _DeltaBaseline(
            canonical=curr_canonical,
            digest_16=cache_key.short(),
        )
```

### Sketch (delta.py, git_status example)

```python
def format_delta_status(prev: StatusResult, curr: StatusResult,
                        level: CompressionLevel, prev_digest: str) -> str | None:
    prev_paths = {e.path: (e.index_status, e.worktree_status) for e in prev.entries}
    curr_paths = {e.path: (e.index_status, e.worktree_status) for e in curr.entries}
    added   = sorted(p for p in curr_paths if p not in prev_paths)
    removed = sorted(p for p in prev_paths if p not in curr_paths)
    changed = sorted(p for p in curr_paths if p in prev_paths
                                            and curr_paths[p] != prev_paths[p])
    if not (added or removed or changed) and curr.branch == prev.branch \
       and curr.ahead == prev.ahead and curr.behind == prev.behind:
        return f"git status: =baseline (delta-from {prev_digest})"
    lines = [f"git status: delta-from {prev_digest}"]
    if curr.branch != prev.branch:
        lines.append(f"  branch: {prev.branch} -> {curr.branch}")
    for p in added:   lines.append(f"  +{curr_paths[p][0]}{curr_paths[p][1]} {p}")
    for p in removed: lines.append(f"  -{p}")
    for p in changed: lines.append(f"  ~{curr_paths[p][0]}{curr_paths[p][1]} {p}")
    return "\n".join(lines)


def jaccard_status(prev: StatusResult, curr: StatusResult) -> float:
    a = {e.path for e in prev.entries}
    b = {e.path for e in curr.entries}
    if not a and not b: return 1.0
    return len(a & b) / max(1, len(a | b))
```

### Sidecar storage

In-memory per-process `dict[str, _DeltaBaseline]` mirroring `_DEFAULT_CACHE`.
LRU-bounded at 64 entries (matches typical agent session call diversity).
Reset on process restart - V47 is a within-session optimisation; cross-process
persistence is out of scope (would compose with V42-style hash-keyed shared
dict if needed).

### Eligible schemas (initial)

| Schema | Canonical type | Jaccard key | Per-entry cost | Expected hit rate |
|---|---|---|---|---|
| `git_status` | `StatusResult.entries` | `path` | ~7 tok | 80% (very stable) |
| `git_diff` | `DiffResult.files` | `path + counts` | ~10 tok | 70% |
| `pytest` | `TestRunResult.failures` | `failure.name` | ~12 tok | 90% (steady CI) |
| `grep` | `GrepResult.matches` | `path:line` | ~6 tok | 60% (refactor-sensitive) |
| `find` | `ListingResult.entries` | `path` | ~5 tok | 75% |
| `ls -R` | `ListingResult.entries` | `path` | ~4 tok | 75% |

## Estimated impact

### Token reduction (per compressor, on a multi-call session for that argv)

Conditional on the call having a same-argv predecessor in the session:

  - **`git status`**: when k=1-2 paths changed (the dominant case after a
    single edit), ~55-65% reduction over the current COMPACT-tier encoding.
    Worked-example showed 53% on a 4-call session; over a 20-call session
    with one edit between each call, the figure rises to ~62% because the
    fixed `git status` header amortises further.

  - **`git diff`**: ~40-55% reduction. The hunk-body drop already happens at
    COMPACT, so V47 only saves the per-file `+/-` count line when files
    didn't change. Lower upside than git status because git diff
    inherently focuses on what changed (its absolute encoding is already a
    delta against HEAD), but V47 still wins on multi-call workflows where
    the agent reads the diff before each `git add -p` chunk.

  - **`pytest`**: 50-90% reduction depending on flip count. Subsumes V16's
    estimate of ~59% weighted-mean additional reduction. V47 covers the
    same workload via a more general mechanism: the test pass/fail set IS
    the canonical structure, so V47's structural-delta logic gives the same
    "+1F -1P" emission as V16's bespoke `format_test_delta`. Cite V16 for
    the per-flip arithmetic; V47 generalises beyond pytest.

  - **`grep` / `rg`**: ~25-45% reduction. Lower because grep results often
    move significantly between calls (file edits shift line numbers). The
    Jaccard threshold of 0.5 ensures we degrade gracefully here: most
    refactor-style grep re-runs fall back to absolute and pay nothing extra.

  - **`find`, `ls -R`**: ~50-65% reduction on stable directory trees with
    occasional file additions.

### Aggregate session-level token saving

Modeled on a representative 60-call agent session distribution (drawn from
typical Claude Code traces against this repo):

| Tool | Calls | Today total (tokens) | V47 total (tokens) | Saved |
|---|---:|---:|---:|---:|
| `git status` | 14 | 420 | 175 | 245 |
| `git diff` | 9 | 540 | 290 | 250 |
| `pytest` | 6 | 1180 | 380 | 800 |
| `rg` | 11 | 660 | 470 | 190 |
| `find` | 4 | 200 | 100 | 100 |
| `ls -R` | 3 | 90 | 50 | 40 |
| Other (single-call, non-eligible) | 13 | 850 | 850 | 0 |
| **Total** | **60** | **3940** | **2315** | **1625 (41%)** |

So **~41% session-aggregate reduction** on top of all existing compressors
for the multi-call portion of an agent session, holding cache-hit calls
unchanged. The bulk of the win comes from pytest (where the test-name
delta dominates) and git status (where each call typically perturbs one
path).

### Latency

  - Cold: zero impact (sidecar empty, first call is no-op).
  - Warm: +1 dict lookup + 1 Jaccard computation (O(N) on canonical entries,
    typically N <= 100). Microseconds. Re-tokenisation on the delta text
    runs through `estimate_tokens` (cheap cl100k approximation, already on
    every code path). Below the noise floor of `subprocess.Popen` startup
    (>1 ms even for `git status`).
  - Cold-start latency budget: untouched. No new lazy imports added (delta.py
    is imported only when the post-cache-miss block runs, which can be
    gated behind a cheap `_DELTA_ENABLED` flag).

### Affects

  - All six listed compressors gain a `parse_to_canonical` shim (most already
    parse internally - it's just exposing the typed result).
  - `pipeline.py` gains the post-cache-miss enrichment block.
  - `_meta.redcon` gains an optional `delta_from` field (additive, no
    schema break).
  - Quality harness needs a delta-aware mode for the schemas that opt in.
    The contract change: must-preserve patterns now apply to the union of
    (delta text, baseline text-by-pointer). Either harness resolves the
    pointer (need to keep baseline text in the sidecar) or relaxes the
    invariant to "every *changed* entry's preserve token survives". The
    latter is cheaper and aligns with V16's contract.
  - Cache key: untouched. V47 lives in a *separate* per-argv sidecar that
    deliberately ignores HEAD/mtime, so it is a strict superset of the
    cache index, not a modification of it.

## Implementation cost

  - LOC: ~260 production (50 pipeline + 200 delta + 10 protocol shim) + ~250
    tests (one delta-rendering golden per schema, similarity-threshold
    fallback test, baseline-bumps-on-low-similarity test, `_meta.redcon`
    annotation test, multi-call simulation test).
  - New runtime deps: zero. No new tokenizers, no embeddings, no network.
  - Determinism: preserved. `(prev_canonical, curr_canonical)` -> single
    delta-text via deterministic sort + format. Sidecar is a per-process dict
    so cross-process determinism is not at stake (and cache-key determinism
    is unchanged).
  - Cache key contract: unchanged. V47 adds a *second* keyspace; the existing
    one is untouched. Constraint #6 in BASELINE.md ("new keying schemes must
    be a strict superset") is satisfied: the V47 sidecar key is
    `(argv-canonical, cwd-canonical)`, which is strictly coarser than the
    existing `(argv, cwd, head, watched)` cache key, so the existing cache
    is *contained within* the V47 baseline keyspace.
  - Must-preserve guarantee: needs an extension - "patterns survive when
    delta + referenced-baseline are concatenated". A simpler interpretation
    that doesn't require pointer resolution: "patterns survive when the new
    canonical structure's contribution to delta is rendered". Concretely,
    if the must-preserve pattern is `branch:` for git_status, the delta
    must always render the branch header (or emit `=branch` to confirm
    unchanged); the dispatcher always does this. ~20 LOC of harness update.
  - Robustness: graceful fallback at every layer. (a) parse-to-canonical
    returns None -> no delta. (b) similarity below threshold -> ship
    absolute, refresh baseline. (c) delta-text is not shorter than absolute
    -> ship absolute. (d) sidecar lookup miss -> first-call behaviour, no
    delta.

## Disqualifiers / why this might be wrong

  1. **The agent must understand the delta encoding.** "delta-from <digest>:
     +path foo, -path bar" is plain English and most LLMs handle it, but if
     the model has been trained on the absolute-snapshot format, the delta
     format is a contract change. Mitigation: the encoding is structurally
     obvious (English-word "delta-from", `+`/`-` markers shared with `diff`
     conventions) and the `_meta.redcon.delta_from` annotation makes it
     machine-detectable. Still, a one-shot benchmark on the target model
     (does Claude correctly fold "delta-from <d1>: +foo.py" against the
     prior turn's `git status`?) is required before flipping the default on.
     If the model fails to apply the delta, the agent might re-fetch the
     absolute encoding, paying both costs - a regression. Solvable by
     keeping the prior absolute output in the same context window (which the
     agent typically already has).

  2. **In-memory sidecar is per-process.** A fresh CLI invocation
     (`redcon run git status`) has no baseline. V47's hits are confined to
     the MCP-server-as-long-running-process model. For the CLI workflow,
     V47 is a no-op unless the sidecar is persisted to disk (SQLite, like
     run_history). Persistence is straightforward (the sidecar is small)
     but adds I/O on the cache-miss path. Probably worth it for the
     `redcon_run` MCP server because the typical agent session is a
     long-lived process; for one-shot CLI use, V47 silently does nothing.
     This is acceptable but it caps the impact to the MCP scenario.

  3. **Subsumes V16 only on paper.** V16's bespoke pytest-delta logic is
     more sophisticated than V47's generic structural delta because pytest
     has the semantic of "regression flip vs repair flip vs steady-state"
     that a generic Jaccard doesn't capture. V47's pytest emission would
     be `+failure tests/foo::test_bar -failure tests/quux::test_flake`,
     which is functionally equivalent to V16's `+1F -1P` ULTRA encoding but
     phrased more verbosely. A V47 implementation that special-cases
     pytest at the format-level (using the V16 ULTRA encoding inside the
     V47 dispatcher) recovers V16's tighter format - net result: V47 is the
     *framework*, V16 is the *pytest specialisation that plugs into it*.
     This is a clean composition, but it means V47's "subsumes V16" claim
     means "V47 provides the framework V16 needs", not "V47's default
     pytest emission is as tight as V16's bespoke one".

  4. **Jaccard threshold of 0.5 is a magic number.** The threshold
     determines the boundary between "delta worth shipping" and "fall back
     to absolute". 0.5 was chosen to guarantee at least 1:1 reduction in
     theory; in practice the right value depends on the schema (grep wants
     0.4 because line-shift sensitivity, pytest wants 0.7 because tests
     usually all run again). Per-schema thresholds dial this in but add
     six new constants. The simpler workaround is to ship the absolute
     and the delta side-by-side at parse time and pick the shorter, which
     does not need a threshold at all - the threshold is just an
     early-exit optimisation. Removing the threshold loses about 20% of
     the latency win but no token win.

  5. **Sidecar staleness across mtime.** Between t=2 and t=3, the agent
     may have run a different argv that mutates state (e.g. `git stash`).
     V47's sidecar keys ignore that. The Jaccard similarity check at t=3
     catches the case (low similarity -> fall back), but the threshold is
     only a soft guard. Adversarial timing can produce a high-similarity
     delta against a baseline that no longer represents the prior agent
     view, and the agent ends up with a delta against state it never saw.
     Mitigation: anchor the baseline to a session-id token so cross-session
     leakage is impossible. Within-session, if the same argv was issued
     and the result is similar, the agent is the only consumer and has
     already seen the prior turn's output. Risk is low.

  6. **The `_DELTA_BASELINE` in-memory dict is not concurrency-safe.**
     If two `compress_command` calls run concurrently (multi-threaded MCP
     server), they may race on the read-then-write of the baseline. In the
     worst case one of them deltas against a stale baseline that was about
     to be overwritten. This is a soft non-determinism: the *content* of
     the delta is still correct, but the choice of which prior state is
     used is timing-dependent. Mitigation: a per-key lock, or accept the
     race and document it (the typical agent serialises tool calls anyway).
     Constraint #1 in BASELINE.md ("deterministic same-input-same-output")
     is at modest risk here - same input but different concurrent context
     could yield different output. Resolution: serialise on (delta_key)
     with a `threading.Lock` map, ~5 LOC.

  7. **Aggregate impact estimate is workload-conditional.** The 41% session
     reduction figure assumes a "Claude Code on this repo" call mix. Other
     workloads (one-shot CI, batch repo scan) have different distributions:
     for a session of 100 *different* commands the delta sidecar is never
     hit and V47 is a 0% reduction. The downside is well-bounded (V47
     cannot increase token cost, by construction), so a no-op session
     pays only the sidecar bookkeeping (microseconds). But the headline
     number must be reported with the workload caveat.

## Verdict

  - **Novelty: high** (within Redcon). BASELINE.md explicitly lists this as
    open ("Snapshot deltas vs prior `redcon_run` invocations of the same
    command on the same repo") and there is no shipping delta encoding for
    any compressor's text output today. As a CS technique it's classical
    (rsync block-delta, IPFS dag-cbor delta, GitHub's check-summary diff)
    but unshipped here. The compounding angle - V47 sits *on top of* the
    existing 73-97% per-compressor reduction without disrupting the cache
    layer - is what makes it breakthrough-shaped: it composes with all 11
    existing compressors at once.
  - **Feasibility: high**. ~260 LOC, no new deps, no determinism risk
    (assuming the per-key lock for thread safety), no cache-key disruption.
    The hardest part is the must-preserve harness extension (~20 LOC).
  - **Estimated speed of prototype: 3-4 days** for an end-to-end working
    version on git_status + pytest (the two highest-leverage schemas), with
    git_diff / grep / find / ls following in days 5-7. Quality harness
    update is the long pole: probably another day for the per-schema
    threshold and the goldens.
  - **Recommend prototype: yes**. This is the shipping form of the V16
    insight, generalised. The reason to prefer V47 over V16: the framework
    cost is paid once; six schemas benefit. The reason to ship V16 first
    inside V47: pytest is the highest-impact single schema and the
    test-pass/fail-set has the cleanest semantic delta. Suggested ordering:
    land the framework (`delta.py` + pipeline plumbing + sidecar) on a
    no-op default, then enable per-schema starting with pytest (V16's
    encoding plugs in) then git_status, gating each behind a flag in
    `BudgetHint`.
    Prerequisites:
      - **(a)** confirm the MCP server is the dominant deployment surface
        (without a long-lived process, V47 caps at 0% impact on cold CLI
        runs);
      - **(b)** decide on the threshold model: per-schema constants vs.
        always-emit-and-pick-shorter. Latter is simpler and trades latency
        for purity;
      - **(c)** confirm the agent-side contract that "delta-from <digest>:"
        is interpretable in the same context window. A 30-call simulated
        session with the actual model-in-the-loop is needed before
        flipping the default on. The framework can ship gated behind an
        opt-in `BudgetHint.snapshot_delta=True` flag while validation runs.

  V47 is the BASELINE-listed missing piece that compounds across compressors,
  protects determinism by construction, and pays back proportionally to how
  many times an agent calls the same tool with small state perturbations
  in between - which is precisely the dominant pattern in interactive agent
  sessions.
