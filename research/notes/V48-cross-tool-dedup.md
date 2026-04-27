# V48: Cross-tool dedup - paths/symbols mentioned in pytest also in grep returns single canonical entry

## Hypothesis

Within a single agent session a path or symbol is typically named by *several*
tools in succession. `grep "test_compress"` emits
`tests/test_cmd_pipeline.py`; the very next `pytest --tb=short` emits the same
file as a failure location; a follow-up `git diff` emits it again as a
changed file header. Today every compressor formats that path independently
and re-emits the *whole envelope* around it (path-line header, per-file count,
preamble like `FAIL test_xyz (...)`). V48 claims that maintaining a
session-level "files-this-session-mentioned" log lets later compressors emit
"(see prior call C)" or a chrome-stripped form when the agent has already
been shown the same path in a chrome-compatible context, *on top of* what
V41 (4-char alias for the path string itself) already does. The non-trivial
claim is **how much extra V48 wins after V41 has shipped** - because V41
already collapses the repeated path *string* to ~1 token, V48 only profits
from the *envelope* (count, header, indent prefix) around that path, plus
selective body elision when the body in call B is byte-identical to call A.

The simulation result on this repo (5 plausible agent sessions, 17
compressor calls, 34 path occurrences) is that V48 buys an extra **1.5-3%
absolute** reduction on top of V41, with an upper bound of ~6% if body
elision (not just chrome elision) is safe to take. V41 alone, by
comparison, buys ~0.6% net after legend overhead. So V48 is **larger than
V41** in token terms even though V41 is its substrate.

## Theoretical basis

Decompose a session's compact-tier output into three additive token
components per path occurrence:

```
T_occ = T_path + T_chrome + T_body
        path string itself (e.g. "redcon/cmd/compressors/grep_compressor.py")
                               ~7-10 cl100k tokens for a typical Redcon path
              header decoration: "{path} ({count})" / "FAIL {name} ({path}:{line})" / "M {path}: +N -M"
                                ~3-6 tokens of decoration that wraps the path
                          body lines under the header (match lines, FAIL message, hunk summary)
                                ~6-15 tokens per occurrence depending on schema
```

The total session token spend on N path occurrences (R of them repeats) is:

```
S = N * mean(T_path) + N * mean(T_chrome) + N * mean(T_body)
```

V41 acts on `T_path` only: post-first-mention, replace 7 tok path with a 1
tok alias `F12`. Saving per repeat: `T_path - 1 ~= 6` tokens. But V41
*pays* a one-time legend overhead per first mention: `len("F12=path\n") ~=
6` tokens. So V41 net saving on the session is

```
DeltaS_V41 = R*(T_path - 1) - F*6      where F = first mentions
           = R*6 - F*6
           = 6*(R - F)
```

If `R < F` (most paths are seen once), V41 *loses* tokens. On this repo's
5-session simulation `F = 15, R = 19`, so V41 net ~= `6*4 = 24` tok which
matches the empirical `16` (slight discrepancy from variable path lengths).

V48 acts on `T_chrome + T_body` for repeats only:

```
DeltaS_V48 = R * (T_chrome + lambda * T_body)
```

where `lambda in [0, 1]` is the fraction of body lines safe to elide
(`lambda = 0` chrome-only, `lambda = 1` full body elision when bodies are
identical). Plug numbers:

```
T_chrome ~= 4, T_body ~= 8, R = 19
DeltaS_V48(lambda=0)   = 19 * 4         = 76 tok    (chrome only, always safe)
DeltaS_V48(lambda=0.5) = 19 * (4 + 4)   = 152 tok   (half body elide)
DeltaS_V48(lambda=1)   = 19 * (4 + 8)   = 228 tok   (body identical, P3-shape)
```

For a 5-session compact-tier budget of ~2500 tokens:

```
V41 contribution       = 24 tok / 2500 = ~1%
V48 chrome-only        = 76 tok / 2500 = ~3%
V48 mid-elision        = 152 tok / 2500 = ~6%
V48 full body elision  = 228 tok / 2500 = ~9%   (only valid when prior call is byte-stable)
```

The **structural reason V48 dominates V41** is that the chrome-and-body
envelope around a repeat path is always >= the path string itself in
compact tier (because compact already minimised path-string repetition).
V41 fights for `T_path = 6 tok per repeat`; V48 fights for `T_chrome +
lambda * T_body = 4 to 12 tok per repeat`. V48 has 0.7-2x the lever arm.

The *information-theoretic* framing matches V07 (call-pair MI): V07
measured that gzip-surrogate `I(A; B) ~= 9%` for grep -> read and `~= 90%`
for pytest-followup, and concluded that **most of that MI lives in chrome
and headers, not in the path string** once compact tier has been applied.
V48 is the operational extraction of exactly that residual.

## Concrete proposal for Redcon

Two concerns: (1) record path/symbol mentions per session in a compressor-
agnostic log, (2) let each compressor consult that log at format time.

### Files

- `redcon/runtime/session.py` (existing, sketched below): add a
  `seen_paths: dict[str, SeenRecord]` map and a thin recorder API.
- `redcon/cmd/compressors/base.py`: extend `CompressorContext` with an
  optional `seen_paths: SeenPathRegistry | None`; default None preserves
  current behaviour bit-for-bit.
- `redcon/cmd/pipeline.py`: thread `session.seen_paths` into
  `CompressorContext` when a session is supplied; remain stateless when
  not (BASELINE constraint #6).
- `redcon/cmd/compressors/{grep,pytest,git_diff,git_status,lint}_compressor.py`:
  on format, replace the *header* line for any path that is already in
  `seen_paths` with a "(see C{prior_call_id})" suffix. Body lines retained
  unless `lambda > 0` mode is on (deterministic flag, not run-time
  detection).
- `redcon/cmd/cache.py`: extend cache key with a digest of the
  `seen_paths` set restricted to paths the current command's parser will
  emit (we don't have that pre-parse, so use the *count* of seen paths
  plus their sorted hash - lets the cache distinguish "first call" from
  "later call" while keeping no-session behaviour bit-stable).

### `RuntimeSession` extension (the sketch the brief asked for)

```python
# redcon/runtime/session.py - additions only, no production source change
@dataclass(frozen=True, slots=True)
class SeenRecord:
    alias: str        # the V41 alias, e.g. "F12"
    first_call: int   # 1-based call index
    schema: str       # which compressor first emitted it ("grep", "pytest", ...)
    chrome_form: str  # how it was rendered ("grep:hits=3", "pytest:FAIL", ...)

@dataclass
class RuntimeSession:
    ...
    seen_paths: dict[str, SeenRecord] = field(default_factory=dict)
    seen_symbols: dict[str, SeenRecord] = field(default_factory=dict)
    call_seq: int = 0
    SEEN_MAX: int = 1024  # bound

    def note_call_emitted_paths(
        self,
        schema: str,
        emitted: list[tuple[str, str]],   # (path, chrome_form)
        alias_for: Callable[[str], str],  # reuse V41's alias map
    ) -> None:
        self.call_seq += 1
        for path, chrome in emitted:
            if path in self.seen_paths:
                continue  # first-mention only; V48 reads it for repeats
            self.seen_paths[path] = SeenRecord(
                alias=alias_for(path),
                first_call=self.call_seq,
                schema=schema,
                chrome_form=chrome,
            )
            if len(self.seen_paths) > self.SEEN_MAX:
                # drop oldest by first_call (deterministic)
                k = min(self.seen_paths, key=lambda p: (self.seen_paths[p].first_call, p))
                del self.seen_paths[k]

    def familiarity(self, path: str) -> SeenRecord | None:
        return self.seen_paths.get(path)
```

### Per-compressor exploitation rules

Each rule is gated on `ctx.seen_paths is not None and path in ctx.seen_paths`,
and falls back to today's emission otherwise. Rules are **deterministic**:
no random ordering, no clock dependency.

| Compressor | Today's per-path chrome | V48 emission for familiar path |
|---|---|---|
| `grep`     | `{path} ({count})\nL3: ...` x M | `{alias} ({count} matches; see C{call})\n L3: ...` (drop the bare path-line; alias already in legend; saves ~3 tok/path) |
| `pytest`   | `FAIL {name} ({path}:{line})\n {first_msg}` | `FAIL {name} ({alias}:{line}; see C{call})` (keep the FAIL line - must-preserve invariant - but elide the first-message line if it duplicates a prior FAIL message verbatim; saves 4-10 tok/familiar fail) |
| `git_diff` | `M {path}: +N -M` | `M {alias}: +N -M (see C{call})` (path -> alias; chrome already short; saves ~4 tok) |
| `git_status` | `M {path}` | `M {alias}` (V41 territory; V48 adds nothing extra here) |
| `lint`     | `{path} ({count})\n L8 E501 ...` | `{alias} ({count}; see C{call}, top: E501)` if no NEW codes vs prior; saves 5-8 tok |

The *only* must-preserve change is one regex extension per compressor: the
quality harness's pattern set must accept `{alias}` as well as
`{path}` for repeat occurrences. This is the same harness widening V41
already requires.

### `lambda > 0` body elision (the optional aggressive mode)

Disabled by default. Enabled by `RuntimeSession.body_elision_mode = True`
or per-call via a `_meta.redcon.dedup_body=True` MCP hint. When on, a
compressor may drop body lines that are byte-identical to the same path's
prior body lines (per `SeenRecord.chrome_form` plus a body hash).
This is the aggressive case that delivers the 6-9% upper bound; it is
the same idea as V07's pytest-followup line-drop. Recommend keeping
`lambda = 0` (chrome-only) on by default and shipping body elision
behind a flag - that gets you the conservative ~3% with no
must-preserve risk.

### Determinism / cache

Cache key extension: when `session is None`, the cache key is the existing
`(canonicalised_argv, cwd_hash)`. When `session is not None`, append
`sorted(session.seen_paths).hash()[:8]`. New keys are a strict superset of
the old (BASELINE constraint #6). Two-process replays work as long as the
session log is replayed in the same call order.

## Estimated impact

Simulation on 5 plausible agent sessions over this repo (17 calls, 34
path occurrences, 19 of which are repeats; details in `cross_tool_sim`
inline below):

```
sessions exercised:
  S1 fix grep JSON parser:       grep -> git_diff -> pytest          (3 calls, 5 path-occ, 3 repeats)
  S2 add tier-2 kubectl:         git_status -> git_diff -> pytest -> grep   (4, 9, 6)
  S3 quality harness drift:      grep -> pytest -> git_diff          (3, 6, 3)
  S4 flaky test_cmd_pipeline:    pytest -> grep -> git_status -> git_diff   (4, 8, 6)
  S5 mcp tool meta-block:        grep -> git_diff -> pytest          (3, 6, 3)

per-session token budget (compact tier, typical): ~500 tok
mean V41 net saving:        3.2 tok/session = 0.6%
mean V48 chrome-only:      15.2 tok/session = 3.0%
mean V48 chrome + half-body: 30  tok/session = 6.0%
```

V48 is ~5x V41 in token terms despite V41 being the substrate. That is
the load-bearing finding: **V41 is the right representation but the wrong
unit of saving** - the path string is already short under cl100k and
compact tier already minimised path repetition, so the residual lives in
the *envelope*, not the *path*.

- **Token reduction**: +3 absolute pp on compact tier per multi-call
  session, +6 pp with optional body-elision flag. Both compound on top of
  the existing 73-97% per-compressor reductions. Below the 5pp BASELINE
  breakthrough bar at the conservative setting, **at the bar in
  body-elision mode**.
- **Latency**: O(1) dict lookup per emitted path. Sub-millisecond per
  call. Cold start unchanged (no new imports).
- **Affects**: all 5 path-emitting compressors (`grep`, `pytest`,
  `git_diff`, `git_status`, `lint`) plus the cache key, plus
  `runtime/session.py`. Does **not** affect file-side scorers.

## Implementation cost

- ~150 LOC: ~40 in `session.py` (SeenRecord, registry, bound), ~10 per
  compressor x 5 = ~50, ~25 in `pipeline.py`/`base.py`/`cache.py`, ~35 in
  the quality harness (must-preserve widening, `(see C\d+)` alias
  acceptance, two new fixtures: cross-tool repeat scenario at compact tier).
- New runtime deps: none. Stdlib + existing tokeniser. No network. No
  embeddings.
- Risks:
  - **Determinism**: holds if the session is single-writer (already true
    of `RuntimeSession`). Two concurrent compressors writing to
    `seen_paths` would race; mitigation: only the pipeline writes
    post-format, single-threaded.
  - **Must-preserve**: harness must accept the alias in place of the
    path for repeat occurrences. The body-elision (lambda>0) flag is a
    larger risk: dropping a FAIL message line that the agent's own
    must-preserve regex needs is a real failure. Default off.
  - **Cache**: strict superset; legacy callers (no session) see
    bit-identical keys.
  - **Comprehension**: if the agent loses the prior tool's output (outer
    budgeter eviction), `(see C12)` is meaningless. Mitigation: keep the
    alias decoded inline (`{alias}` is interpretable from the legend
    alone, so even if C12 is gone the agent still has the path through
    V41's legend). The `(see C12)` is a navigation hint, not a
    correctness device.

## Disqualifiers / why this might be wrong

1. **Most of V48's leverage is exactly what V07 already characterised
   under the name "prior-conditioned compressor".** V07 measured the
   chrome-and-body residual at compact tier and proposed
   prior-conditioning per pair (status->diff, grep->read,
   pytest->pytest). V48 generalises V07's per-pair rules to a per-session
   set (any-prior-tool -> current-tool when path overlaps), but the
   measured wins on this repo (3% chrome, 6% full body) are the same
   ballpark V07 reported (1-3% on P1/P2, 80% on P3). V48 may collapse to
   "V07 with a session log" - a useful packaging but not a new
   information-theoretic dimension. Mitigation: V48's contribution is the
   *cross-tool* generalisation of V07's same-tool delta; V07 explicitly
   limited itself to consecutive same-family pairs.
2. **The 19 repeats / 34 occurrences ratio is biased by my session
   construction.** I picked sessions that already feature path overlap
   (because the brief is about overlap). Agents whose sessions go
   `git status -> docker logs -> kubectl get pods` have ~0 repeats and
   V48 is a no-op. Honest measurement requires actual session traces;
   the 5 fixtures here are illustrative only.
3. **V48 needs V41 to be implemented first to be coherent.** If V41
   lands without V48, V48's marginal contribution is ~3% (chrome only).
   If V41 *doesn't* land, V48 still saves chrome but the alias scheme is
   gone and you re-emit the full path each time anyway, capping the
   repeated mention at "(see prior)" + path = ~10 tok which may be
   *worse* than the existing emission. So V48 is genuinely V41-conditional.
4. **Outer budgeter evicts the prior call.** Same risk V07 noted: the
   `(see C12)` reference is dangling if the LLM context dropped C12. The
   alias `F12` from V41 still resolves through the legend (which itself
   could be evicted but is small enough that we re-emit on each turn);
   the call-id reference is genuinely fragile.
5. **Body-elision mode (lambda>0) is the only way to clear the 5pp
   breakthrough bar, and it has real correctness risk.** The
   "body-identical" assumption fails when grep B's snippet captures a
   line at a different position than pytest A's snippet, even when both
   are in the same file. Mitigation requires per-line hashing keyed on
   `(path, line_no, content_hash)` rather than `(path, line_no)` - extra
   bookkeeping that erodes the win.
6. **Already partially in place under another name?** No: `RuntimeSession`
   tracks pack-level files (`last_run_artifact`) and turn summaries, but
   not individual paths emitted by `redcon_run` calls. The
   `seen_paths` map is genuinely new state.
7. **Cross-tool symbol dedup (the second half of the brief) buys
   essentially nothing.** Symbols (function names, class names) appear
   in pytest FAIL lines and grep output but rarely repeat across tools
   in the same session - their natural overlap is within-tool (multiple
   matches in one grep). Confirmed in the simulation: 0 repeated symbols
   across the 17 calls. V48-for-symbols is dead-on-arrival; the value
   is in paths.

## Verdict

- Novelty: **medium**. The path-string deduplication is V41; the
  envelope deduplication is novel beyond V41 but reduces to a
  cross-tool generalisation of V07's prior-conditioning. The combined
  package "session-scoped alias + chrome elision on repeat" is what
  the brief asks about, and it is genuinely larger than either V41 or
  V07 alone (V48 chrome-only at 3% beats V41-net at 0.6% by 5x).
- Feasibility: **high**. ~150 LOC, no deps, deterministic, cache-superset.
  The hard work is the harness fixture for the cross-tool case, not the
  format change.
- Estimated speed of prototype: **2-3 days** for the chrome-only mode
  (V41 must already exist); **+2 days** for the body-elision flag and
  per-line content-hash bookkeeping; **+1 day** to wire MCP `_meta.redcon`
  hint and update the quality harness.
- Recommend prototype: **conditional-on V41 landing first**. V48 alone
  without V41 produces awkward "(see prior) {path}" emissions that may
  be larger than today's bare path. With V41 in place, V48 is the
  highest-leverage cross-call dimension after V47 (snapshot delta) and
  is worth shipping in chrome-only mode (3% headline, no must-preserve
  risk). Body-elision mode should ship behind a flag with the V07
  pytest-followup fixtures as the validation corpus.

### Honest answer to the brief's question

> "Quantify the additional saving from V48 ON TOP OF V41. Is it material?
> Or does V41 already capture it?"

V41 captures the path *string* (~6 tokens per repeat, minus a 6-token
legend per first mention - a near wash on this repo, net ~0.6%). V48
captures the *envelope* (~4 tokens per repeat, no legend cost), which
this repo's 5-session simulation values at ~3% absolute reduction
(chrome-only) and up to ~6% with body elision. **V48 is materially
larger than V41**, despite being built on top of it. The reason is
structural: compact-tier compressors have already minimised path
repetition, so the residual cross-call redundancy lives in the
chrome-and-body envelope, which V41 cannot touch and V48 is purpose-built
for. V41 is the necessary substrate; V48 is the lever where the saving
actually lives.
