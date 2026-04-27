# V71: Content-defined chunking (FastCDC) for cache key under near-duplicate argv

## Hypothesis

Today `redcon/cmd/cache.py::build_cache_key` keys the per-process compression
cache on canonicalised `argv + cwd + git HEAD + watched-mtimes`. Two argv
strings that produce byte-identical subprocess output (e.g. `git diff` vs
`git diff HEAD`, `git status -s` vs `git status --short`, `rg foo` vs
`rg --color=never foo`) miss the cache and pay the full cost of running the
compressor + tokenizer even though the resulting `CompressionReport` would be
byte-for-byte identical. The claim of V71: introduce a *second* cache layer
keyed on a content digest of the raw `(stdout, stderr)` bytes computed via
FastCDC chunking + concatenated chunk hashes (or, equivalently here, a single
SHA-256 of the raw bytes - chunking buys nothing for the present in-memory
case but matters for the disk-spilled and cross-process variants discussed
below). On argv-cache miss, after `run_command` returns but *before*
`compressor.compress` runs, hash the raw output. If a content cache hit
exists, reuse the prior `CompressedOutput` and skip both the parser and the
tokenizer. The argv cache continues to short-circuit the subprocess. The
content cache only avoids the *post-subprocess* CPU. Output-keyed cache layer
on top of input-keyed cache layer.

The honest framing - which the methodology section of this brief explicitly
demands - is that this saves compressor CPU and never saves a single token,
nor does it avoid the subprocess (which is where wall time mostly lives). The
question is whether the frequency of "different argv, same output" is high
enough to be worth a second hash table and a SHA-256 of every raw output.

## Theoretical basis

### 1. What FastCDC actually buys

FastCDC (Xia et al., ATC 2016) is a content-defined chunker designed for
deduplication storage. Given a byte stream, it walks a rolling Gear hash and
declares chunk boundaries when the hash satisfies a mask predicate, with
average chunk size tuned by the mask width. Properties relevant here:

  - O(n) single-pass, ~3 GB/s on commodity x86 (paper Table 4).
  - Chunks are stable under *insertion* of bytes - inserting K bytes into a
    file shifts at most one chunk boundary, so K bytes' worth of unchanged
    content downstream still hashes to the same chunks.
  - Chunk-set Jaccard is a usable similarity metric: if two outputs share a
    fraction p of chunks, dedup ratio is (1 - p).

For the V71 use case (cache key under near-duplicate argv), the *input* to
chunking would be the raw subprocess output. The dedup happens not within a
single output but across multiple outputs from different argv. The hash key
is then either:

  - **(a)** SHA-256 of the full byte stream - 32 bytes, single hash, same
    semantics as a "trivial CDC of one big chunk". This is what we actually
    need when the criterion is "byte-identical output".
  - **(b)** Multiset of chunk hashes - useful if "near-duplicate output"
    (e.g. one extra line at the end) should also hit. This is where FastCDC
    earns its name. Costs more (one hash table per chunk, plus a similarity
    join step at lookup time) and trades determinism for fuzzy reuse.

For a deterministic-by-construction project with a "byte-identical or miss"
contract, (a) is sufficient and (b) introduces a fuzzy match that violates
constraint #1 (deterministic same-input-same-output) unless the output of the
content cache for a partial hit is a fresh re-compression that incorporates
the new bytes. (b) is essentially a delta encoder, which is V47's territory
and is well-handled there.

So: FastCDC is the wrong tool. SHA-256-of-stdout is the right tool. V71
collapses to "add a second cache layer keyed on `sha256(stdout || 0x00 ||
stderr)`". The FastCDC framing in the vector title is a misdirection - I
should call this what it is.

### 2. Frequency estimate: how often does same output come from different argv?

Let A be the set of argv normalisations that produce identical output for a
given repo state. For Redcon's command surface, A partitions by command:

  - `git diff` and `git diff HEAD` produce identical output iff there are no
    staged changes. In a typical agent session this holds in ~30-50% of
    `git diff` calls (the agent runs `git add` then `git diff` and the
    unstaged set is empty; subsequent `git diff` is empty bytes, identical
    to `git diff HEAD` when nothing is staged either).
  - `git status -s` and `git status --short` are exact aliases. Identical
    bytes always. But: the rewriter (`redcon/cmd/rewriter.py`) does not
    canonicalise `--short` -> `-s` today, so these miss the argv cache.
  - `rg foo` and `rg --color=never foo` differ only in ANSI escapes.
    `rg` since 13.x defaults to `--color auto` which is `never` when stdout
    is not a TTY (it is not in subprocess.Popen). Identical bytes.
  - `ls -la` and `ls -al` are flag permutations. Distinct argv strings;
    identical bytes.
  - `pytest` and `pytest .` are identical when run from repo root.
  - `find . -type f` and `find -type f` (BSD vs GNU find tolerance varies).

The frequency at which two consecutive calls in the same session have the
same output despite different argv is, empirically:

    P(same output | different argv, same command) ~ 0.05 - 0.15

based on a back-of-envelope inspection of `redcon/cache/run_history_cmd`
across 60-call sessions. The argv cache catches identical-argv calls; the
content cache would catch this 5-15% slice.

### 3. What the content cache actually saves

When the content cache hits, the savings are:

    saved_per_hit = T_compressor + T_tokenize + T_normalise

Concretely, on the existing benchmark numbers from `redcon/cmd/benchmark.py`:

    T_compressor (git_diff parser):    ~3-12 ms
    T_tokenize  (cl100k _tokens_lite): ~1-4 ms
    T_normalise (whitespace re-pass):  <1 ms

Total ~5-17 ms per content-cache hit. The subprocess itself was already paid
(content cache doesn't avoid `run_command`). So V71 saves CPU but never wall
time below the subprocess floor (~10-50 ms for `git status`, ~20-200 ms for
`git diff`, ~50-2000 ms for `pytest`). On a 60-call session with ~10% content
hit rate among argv-cache misses (~25 calls remaining after argv hits), V71
saves:

    saved = 25 * 0.10 * 11 ms ~= 28 ms per session

That is below the noise floor of a single tool round-trip and is essentially
zero compared to the 5-30 second wall-clock of an agent turn dominated by
LLM latency. The token saving is exactly **0**.

### 4. Crucial token comparison: V71 vs argv canonicalisation

The vector raises `git diff` vs `git diff HEAD` as the motivating example.
But these only produce identical output in a specific repo state; in general
they differ. The cleaner solution to the *argv-aliasing* part of the problem
is to extend `redcon/cmd/rewriter.py` with explicit alias canonicalisation:

    "git status --short"   -> "git status -s"
    "rg --color=never X"   -> "rg X"        (always, since rg in pipe is never anyway)
    "ls -al"               -> "ls -la"      (canonical flag order)
    "git diff HEAD"        -> "git diff"    (only if no staged changes - state-dependent, skip)
    "pytest ."             -> "pytest"      (when cwd is repo root)

Of these only the state-independent ones (`-s`/`--short`, color aliases,
flag-order canonicalisation) belong in the rewriter. They convert "different
argv same output" cases into "same canonical argv same output" cases, which
the existing argv cache handles for free with no second hash table. The
state-dependent ones (`git diff` vs `git diff HEAD`) cannot be canonicalised
safely and are exactly the V71 sweet spot. There are perhaps 2-4 such pairs
per command in real use. The total argv-cache extension would land ~80% of
V71's content-cache hits at zero new infrastructure cost. So most of V71's
target frequency dissolves into the existing rewriter.

### 5. Composition with log-pointer tier

The pipeline already spills to disk when raw bytes > 1 MiB. For those calls
the content cache key is exactly what we want as the spill-log filename
(currently `<argv-cache-digest>.log`; under V71 it could be `<sha256-of-raw>
.log`, dedup'ing identical 100 MB docker build logs across two slightly
different `docker build` argv). This is a real win: large-output dedup
*does* save disk and *does* save tokenizer time on the tail-30-line summary
recomputation. But that's a small wedge of a small wedge.

### 6. Cost model

```
                           argv-cache       V71 content cache
hit identifies identical:  argv+cwd+head    bytes
saves subprocess:          yes              no
saves compressor:          yes              yes
saves tokenizer:           yes              yes
saves token streaming:     yes (zero text)  yes (cached text)
hit rate (typical):        58%              5-15% of remaining 42%
                                            = 2-6% of all calls
```

The marginal value of the content cache, given the argv cache, is the 2-6%
slice multiplied by the 5-17 ms per hit. Aggregate: <0.1 second per session.
Token impact: zero.

## Concrete proposal for Redcon

### Files touched

  - **`redcon/cmd/cache.py`** (~30 LOC): new `build_content_key(stdout,
    stderr) -> ContentCacheKey` function. SHA-256 over `stdout || 0x00 ||
    stderr`. Returns a frozen dataclass with `digest: str` and a `short()`
    method matching the existing `CommandCacheKey` API.

  - **`redcon/cmd/pipeline.py`** (~25 LOC): post-subprocess, pre-compressor
    branch:

```python
# After run_command returns, before detect_compressor / spill / passthrough:
content_key = build_content_key(run_result.stdout, run_result.stderr)
if effective_content_cache is not None:
    cached = effective_content_cache.get(content_key.digest)
    if cached is not None:
        # Different argv, same output -> reuse prior compressed result.
        # The CompressedOutput is shared across argvs; only the outer
        # CompressionReport (cache_key, raw_bytes, duration) varies.
        report = _rebuild_report(
            output=cached.output,
            cache_key=cache_key,
            raw_stdout_bytes=len(run_result.stdout),
            raw_stderr_bytes=len(run_result.stderr),
            duration_seconds=run_result.duration_seconds,
            returncode=run_result.returncode,
        )
        effective_cache[cache_key.digest] = report  # populate argv cache too
        return _with_cache_hit(report)
```

  - **`redcon/cmd/rewriter.py`** (~40 LOC): the *better* delivery of the same
    underlying intent. Add explicit aliases:

```python
_GIT_STATUS_SHORT_ALIASES = {"--short": "-s", "--branch": "-b"}
_RG_REDUNDANT_FLAGS = {"--color=never", "--no-heading"}  # default in pipe
def _canonicalise_aliases(argv): ...
```

This lands ~80% of V71's hit rate without a content cache.

### API sketch

```python
# cache.py additions
@dataclass(frozen=True, slots=True)
class ContentCacheKey:
    digest: str   # sha256 hex of stdout||0x00||stderr
    nbytes: int   # for stats

    def short(self) -> str:
        return self.digest[:16]


def build_content_key(stdout: bytes, stderr: bytes) -> ContentCacheKey:
    h = hashlib.sha256()
    h.update(stdout)
    h.update(b"\0")
    h.update(stderr)
    return ContentCacheKey(digest=h.hexdigest(), nbytes=len(stdout) + len(stderr))


# pipeline.py: a parallel _DEFAULT_CONTENT_CACHE
_DEFAULT_CONTENT_CACHE: MutableMapping[str, CompressionReport] = {}
```

### Sidecar cost

  - SHA-256 over typical command outputs (1-100 KiB) costs ~5-50 us at
    ~3 GB/s. Below noise floor. Always-on is fine.
  - One extra `dict.get` per cache miss. Microseconds.
  - Memory: one entry per unique output content per process. LRU bound at 64
    entries (matches argv cache footprint). At ~5 KiB average compressed
    output, ~320 KiB worst case.

## Estimated impact

### Token reduction

**Zero**. V71 changes nothing about the *content* of the compressed output.
The same `CompressedOutput` is emitted; the difference is whether the
compressor and tokenizer ran or were skipped. Token counts on agent context
are identical with and without V71.

This is the disqualifying observation for V71-as-headlined: BASELINE.md's
breakthrough criterion is ">=5 absolute pp reduction across multiple
compressors" or ">=20% cold-start latency cut". V71 is neither.

### Latency

  - Cold: zero impact (cache empty).
  - Warm hit (argv cache): unchanged. V71 lookup is on the cache-miss path
    only.
  - Warm miss + content hit: saves 5-17 ms (one compressor + one tokenizer
    pass). Hit rate 2-6% of total calls. Aggregate ~28 ms / 60-call session.
  - Warm miss + content miss: +5-50 us for the SHA-256 over raw bytes.
    Negligible.

### Affects

  - `redcon/cmd/cache.py`: gains `ContentCacheKey` and `build_content_key`.
  - `redcon/cmd/pipeline.py`: gains a content-cache check in the cache-miss
    path, plus a hook that populates the argv cache *from* a content hit so
    the next identical-argv call short-circuits at the cheaper (argv) layer.
  - `redcon/cmd/rewriter.py`: gains alias canonicalisation that subsumes
    most of V71's target frequency at zero infra cost. This is the one
    actually-recommended deliverable from this research thread.
  - `_meta.redcon` block: optional `content_cache_hit: bool` for telemetry.
  - Quality harness: unaffected. The compressed output is identical to the
    non-cached run (it *was* the non-cached run, on a different argv).
  - Determinism: the content cache is keyed on raw bytes, which are
    deterministic by `run_command` design. No new non-determinism. The
    *contract* it adds: "two argvs that produce identical raw bytes share a
    cache entry," which is a strict superset of the argv cache and is
    therefore compliant with constraint #6 in BASELINE.md.

## Implementation cost

  - LOC: ~95 production (30 cache.py + 25 pipeline.py + 40 rewriter.py for
    alias canonicalisation - the latter is the part with leverage). ~120
    tests.
  - New runtime deps: zero. SHA-256 is in the stdlib. FastCDC is *not*
    needed; if a future variant wants chunked dedup for large logs, it
    would add a ~150 LOC pure-Python FastCDC implementation, no external
    deps.
  - Determinism: preserved. Stable hash, no randomness, no time inputs.
  - Cache key contract: extended. The new keyspace is byte-content-keyed
    and lives parallel to the existing argv keyspace. Both are populated;
    a hit in either short-circuits. Constraint #6 satisfied.
  - Robustness: graceful. Content cache miss -> normal compressor path.
    SHA-256 cannot fail on byte input.
  - Must-preserve: unchanged. The cached output was generated under the
    same harness; reusing it is equivalent to running it fresh and
    producing identical bytes.

## Disqualifiers / why this might be wrong

  1. **Saves no tokens.** BASELINE.md's breakthrough bar is token reduction
     or cold-start latency. V71 saves neither - it saves warm-path
     compressor + tokenizer CPU, which is already in the millisecond range
     and far below subprocess and LLM-roundtrip floors. The argv-keyed
     cache plus warm parsers (already shipped) make the marginal CPU
     savings invisible to the user. Per the methodology requirement to be
     honest: this is the dominant disqualifier and it is structural.

  2. **The motivating examples mostly belong in the rewriter.** `git status
     --short` vs `-s`, `rg --color=never X` vs `rg X`, `ls -al` vs `-la`,
     `pytest .` vs `pytest` are all argv-canonicalisation cases. Extending
     `redcon/cmd/rewriter.py` with ~40 LOC of alias canonicalisation lands
     ~80% of V71's target hit rate with zero new infrastructure and no
     second cache layer. The leftover state-dependent cases (`git diff`
     vs `git diff HEAD` in a no-staged-changes repo) are 2-4% of calls
     and below the worth-implementing threshold.

  3. **FastCDC is the wrong tool.** Content-defined chunking is for
     dedup of *partial* overlap (rsync, dedup storage, IPFS). Redcon's
     content cache needs *full* equality (deterministic same-input-
     same-output). A single SHA-256 over raw bytes is exactly the right
     primitive; CDC adds complexity without semantic value. The vector
     title is misleading - the actually-useful idea here is much simpler.

  4. **Hit rate is bounded above by argv aliasing reality.** The set of
     real "different argv, same output" pairs in agent workflows is
     small. After rewriter aliasing, the residual set is dominated by
     state-dependent equivalences that look like cache hits only by
     accident (empty `git diff` matching empty `git diff HEAD`). Many
     of those are also caught by the existing log-pointer threshold
     when output is tiny - the empty-output case is a 0-byte cache that
     compresses to ~5 tokens regardless of argv, and re-running the
     parser on 0 bytes costs nothing.

  5. **CompressorContext binds to argv.** The cached `CompressedOutput`
     was produced under argv A; reusing it for argv B is technically
     correct only when the compressor's behaviour does not depend on
     argv. Most compressors only read the bytes, but a few read argv
     for context (`git_diff` checks for `--stat`, `pytest` checks
     `--tb=line`). If V71 reuses an output produced under argv A for a
     call with argv B that *would* have triggered different compressor
     behaviour despite producing the same raw bytes, the cached output
     is wrong. Mitigation: include the canonicalised argv in the
     content key, but at that point the content cache is just the argv
     cache plus a redundant SHA-256 over raw bytes, defeating the
     purpose. Cleaner mitigation: include only the *compressor-affecting*
     subset of argv (a per-compressor allowlist), which adds complexity
     and audit surface.

  6. **Memory waste on log-pointer tier.** When raw bytes > 1 MiB, the
     content cache would store... what? The compressed log-pointer
     summary? Across two different argv with identical 50 MB output,
     reusing the summary saves a few hundred bytes of computation. The
     log file itself already lives on disk; the dedup question (do we
     store one log file or two for two argv with identical bytes?) is a
     legitimate disk-dedup question. But the disk-dedup answer is
     content-addressed filenames in `.redcon/cmd_runs/<sha256-short>.log`
     - that's a filename-scheme change in the log spiller, not a cache
     redesign.

## Verdict

  - **Novelty: low**. Output-keyed caching is a textbook idea. SHA-256 of
    raw bytes is one-line. FastCDC framing is a category error - V71's
    actual useful content collapses to "add a second cache keyed on
    `sha256(stdout||stderr)`," which is a minor variation on the existing
    argv cache. The rewriter-extension portion is a routine janitorial
    improvement, not a breakthrough.
  - **Feasibility: high**. ~95 LOC, no new deps, no determinism risk, no
    cache-key contract break. Could land in an afternoon.
  - **Estimated speed of prototype: 4-6 hours** total: 1 hour for cache
    plumbing, 1 hour for rewriter aliases, 1 hour for tests, 1 hour for
    benchmark instrumentation to confirm hit rate, 1-2 hours for review.
  - **Recommend prototype: conditional-on-X**.
    - Recommend **X=YES** for the rewriter alias-canonicalisation portion
      alone (~40 LOC, captures most of V71's target frequency, zero new
      infrastructure, integrates with existing `prefer_compact` rewriter
      design).
    - Recommend **NO** for the content-cache portion as a standalone
      contribution. The token impact is zero, the latency win is below
      noise floor, and the conceptual surface area (a parallel cache,
      argv-vs-content provenance tracking, edge cases around argv-aware
      compressor behaviour) is not justified by 28 ms / session of saved
      CPU.
    - Recommend **YES, REPURPOSED** for the content-key idea applied to
      the log-pointer spill filenames (`.redcon/cmd_runs/<sha256-short>.log`).
      This dedups large logs across argv and is a clean ~10 LOC change in
      `_spill_to_log`. Disk savings only, but real and free.

  Honest summary: V71-as-titled is not a breakthrough; the methodology
  brief explicitly invited this honesty ("argv-keyed cache plus warm parsers
  may already be fast enough... savings here are mostly compressor CPU, not
  tokens. Address."). Addressed: confirmed. The valuable carve-outs are
  (a) rewriter alias canonicalisation, which is worth shipping on its own
  merit, and (b) content-addressed spill log filenames, a nice cleanup.
  Neither requires FastCDC, neither moves the BASELINE-listed needle, and
  neither is the headline V71 claim. The headline claim is dominated by
  the existing argv cache.
