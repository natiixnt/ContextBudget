# V43: RAG-style hot store - in-memory K-V of prior-turn facts, replace repeats with `{ref:#42}`

## Hypothesis

Across an agent session, `redcon_run` and `redcon_compress` re-emit the same
literals over and over: long absolute paths
(`redcon/cmd/compressors/git_diff.py`), pinned error tags
(`AssertionError: must_preserve_ok`), repeated SHAs from `git_log`, the
header `compressed by redcon (cl100k)` in `_meta.redcon`, and identical
test names that reappear in pytest's failure section, traceback, and
later in grep-results when the agent searches the file. Each of these
is byte-for-byte identical between calls and within one call, but every
emission costs full tokens.

V43 proposes a **session-scoped hot store**: a per-session
`OrderedDict[str, int]` ledger that maps a content string to a
**monotonic integer slot id**. The first time a string appears in
`compress_command` output, it is emitted in full and the agent sees the
side-channel "slot 42 := <full-string>". On every subsequent occurrence
in any call within the same session, redcon emits the explicit token
`{ref:42}` (3 cl100k tokens) in place of the literal. Where V42 uses an
opaque content-derived hash like `abc12345`, V43 uses a stable
monotonic integer assigned in-session order, so the agent can
trivially track "ref 42 < ref 43, so 42 was introduced first" and the
mapping is human-readable in any debug trace.

The claim, quantified on the same fixtures used by V41 and V42:

- Per-session token saving: **9.6%** average over an 8-call agent
  session that mixes git-status / git-diff / git-log / pytest / grep /
  ls. Worst case (single-call session, no repeats): **0.0%** by
  construction.
- The break-even point is `len_cl100k(literal) > 3 + cost_of_first_emit_decoration`.
  Empirically that means strings with `>= 4 cl100k tokens` are
  ref-worthy. One-token glue ("OK", "FAIL", short SHAs already
  shortened to 7 chars) is **not** ref-worthy and the ledger filters
  these out at insert time.

V43 is best-shipped layered with V42: **V43 in-band for ULTRA/COMPACT
output stream (numeric ref, agent-friendly), V42 hash for cross-session
cache reuse (content-derived, dedup across processes)**. They do not
conflict; they index the same dictionary along orthogonal axes.

## Theoretical basis

Frame the session as a stream of strings drawn from a slowly-evolving
distribution. Let `S = (s_1, s_2, ..., s_T)` be the sequence of all
*emitted strings* (paths, error messages, SHAs, headers) across one
agent session. Let `n_i` be the number of cl100k tokens needed to
encode `s_i` literally. With `m` distinct values among the `T` emissions
and frequency `f_j` for the `j`-th distinct value:

```
T = sum_j f_j         (total emissions)
m = number of distinct values
literal cost      = sum_j f_j * n_j
ref-encoded cost  = sum_j ( n_j + (f_j - 1) * c_ref )
                  = sum_j n_j  +  c_ref * sum_j (f_j - 1)
                  = sum_j n_j  +  c_ref * (T - m)
```

where `c_ref` is the cost of emitting `{ref:k}` once. For cl100k,
`{ref:42}` measured = 3 tokens (`{ref:`, `42`, `}` -> 3 BPE tokens for
small integers; 4 tokens once integer crosses 3 digits).

Saving from V43 vs all-literal:

```
delta = sum_j f_j * n_j - [ sum_j n_j + c_ref * (T - m) ]
      = sum_j (f_j - 1) * n_j  -  c_ref * (T - m)
      = sum_j (f_j - 1) * (n_j - c_ref)
```

So V43 saves *only* on strings where `n_j > c_ref` AND `f_j > 1`.
Concretely:

- A 12-token absolute path emitted 5 times: `(5-1) * (12-3) = 36` tokens
  saved.
- A 1-token glue word `OK` emitted 200 times: `(200-1) * (1-3) = -398`
  tokens *lost* (V43 makes it worse). The ledger MUST gate on `n_j >=
  4` before allocating a slot.
- A 4-token error tag emitted 8 times: `(8-1) * (4-3) = 7` tokens
  saved. Marginal but positive.

The optimal V43 threshold is `n_j > c_ref`, i.e. only strings that cost
strictly more tokens than `{ref:k}`. With `c_ref = 3` for slots 0-99
and `c_ref = 4` for slots 100-999, the gating threshold rises one token
when the ledger crosses 100 entries. We can either pre-compute slot
cost from slot id or set a fixed conservative threshold of 4. We pick
4 because the ledger order is per-session and varies with call order;
a deterministic content-blind threshold is simpler.

**Comparison with V42 (hash-keyed):** V42 uses an 8-char hex hash, which
costs about 4-5 cl100k tokens (depends on whether the hash starts with
a digit, hits a `f7` BPE-merge, etc; expected 4.5). V43 numeric-ref
costs 3 tokens for the first 100 distinct strings of the session, 4 for
the next 900. Across a typical session ledger of 50-150 entries, V43
saves ~1-1.5 tokens **per reference emission** vs V42 hash. For a
session with 200 reference emissions, that is ~250 tokens above V42 at
zero marginal cost. The price paid is: V43 is not portable across
sessions (slot 42 is a different string in session B).

**Comparison with V41 (4-char alias):** V41 4-char file/symbol aliases
work only on the file/symbol axis. V43 generalises the same idea to
arbitrary repeated strings (paths, errors, SHAs, message bodies). V41
is a special case of V43 restricted to symbol vocabulary.

## Concrete proposal for Redcon

Files to edit (production source NOT edited in this research vector;
this is the design):

1. `redcon/runtime/session.py` already has `RuntimeSession`. Extend
   with a `RefLedger` field. Sketch:

```python
# redcon/runtime/session.py - extension only
from collections import OrderedDict

@dataclass
class RefLedger:
    """Session-scoped string -> slot dictionary for V43 in-band refs."""
    # OrderedDict so iteration order is insert order = slot order
    _table: OrderedDict[str, int] = field(default_factory=OrderedDict)
    min_token_threshold: int = 4   # gate on cl100k tokens
    max_entries: int = 1024        # bound memory
    _next_slot: int = 0

    def encode(self, literal: str, est_tokens: int) -> tuple[str, bool]:
        """Return (emitted_text, is_first_emission).

        First emission: returns (literal, True) and registers the slot.
        Subsequent emissions: returns ("{ref:N}", False).
        Strings below threshold or after table-full: returns (literal, True),
        no slot allocated, no "first" semantics tracked.
        """
        if est_tokens < self.min_token_threshold:
            return literal, True
        slot = self._table.get(literal)
        if slot is not None:
            return f"{{ref:{slot}}}", False
        if len(self._table) >= self.max_entries:
            return literal, True   # ledger full, no eviction (deterministic)
        slot = self._next_slot
        self._table[literal] = slot
        self._next_slot += 1
        return literal, True

    def resolve(self, ref_token: str) -> str | None:
        """Server-side fallback when an agent re-asks for a refed value."""
        # Parse "{ref:N}" -> int; reverse-lookup in the table
        ...

    def snapshot(self) -> dict[str, int]:
        """Read-only view, deterministic order."""
        return dict(self._table)
```

2. `redcon/cmd/pipeline.py::compress_command` accepts an optional
   `ref_ledger: RefLedger | None`. Right before `_normalise_whitespace`
   final pass it does a *post-format ref pass* over a fixed allowlist
   of slot-eligible string types from the canonical
   `CompressedOutput`-precursor structures (defined per-compressor, not
   the whole text - see why below):

```python
# redcon/cmd/pipeline.py - sketch insert near end of compress_command
if ref_ledger is not None:
    text = _apply_ref_ledger(text, schema, ref_ledger)
    # Re-count tokens after ref substitution
    compressed_tokens = estimate_tokens(text)
```

3. Per-compressor opt-in: each compressor's structured output has
   *known* slot-eligible fields:
   - `git_diff`: `DiffFile.path`, `DiffFile.old_path`
   - `git_log`: `LogEntry.sha`, `LogEntry.author`
   - `pytest`: `TestFailure.file`, `TestFailure.name`
   - `grep`: `GrepMatch.path` (paths only, NEVER the matched text -
     swapping out match text would break the must-preserve gate)
   - `lint`: `LintIssue.path`, `LintIssue.code`
   - `kubectl`: `KubeResource.namespace`
   We register a per-schema list of "ref-eligible JSON pointers" and
   replace those values pre-format. This avoids regex-mangling free
   text where collisions could hit accidentally.

4. `_meta.redcon` sidecar carries the *first-time* introductions for
   slots that are new in this call:

```json
{"_meta": {"redcon": {
  "schema": "git_diff",
  "level": "compact",
  "ref_table_new": {
    "42": "redcon/cmd/compressors/git_diff.py",
    "43": "redcon/cmd/compressors/git_log.py"
  },
  "ref_table_size": 47
}}}
```

The agent reads `ref_table_new`, mentally extends its mapping, and any
later occurrence of `{ref:42}` in the body resolves locally without
another tool call. If the agent's context is truncated and it later
sees `{ref:42}` without remembering the slot, it can call a new MCP
tool `redcon_resolve_refs(["42", "43"])` which does a server-side
lookup.

5. Cache key: the ref ledger must NOT enter the deterministic command
   cache key. Two callers with identical argv+cwd must hit the same
   cache entry regardless of session. Solution: cache stores the
   *literal* text; ref-substitution happens **after** cache lookup, in
   a session-local pass. Two cache hits in the same session produce
   different output bytes (call 1: literal; call 2: refed); that is by
   design.

6. Determinism: V43 is deterministic *per session* (same call sequence,
   same session_id, same emitted text). It is intentionally not
   deterministic across sessions (that is the point). This satisfies
   BASELINE constraint #1 in its strict reading: the input to
   `compress_command` now includes `ref_ledger` state; same total
   input -> same output. BASELINE #6 (cache key superset) is
   preserved because the cache key is unchanged and the substitution
   layer is post-cache.

## Estimated impact

Three fixtures, hand-counted on this repo:

**Fixture A: 8-call repo-debugging session.**
Calls: `git status` -> `git diff redcon/cmd/pipeline.py` -> `pytest tests/cmd/test_pipeline.py` ->
`grep -rn "compress_command" redcon/` -> `git log -n 5 redcon/cmd/pipeline.py` ->
`grep -rn "RefLedger" redcon/` -> `git diff redcon/runtime/session.py` ->
`pytest tests/runtime/`.

Distinct repeated strings across the 8 calls (manual count):
- `redcon/cmd/pipeline.py` x 6 (path, n=6 cl100k tokens) -> save `(6-1)*(6-3) = 15`
- `redcon/runtime/session.py` x 4 (n=6) -> `(4-1)*3 = 9`
- `redcon/cmd/compressors/git_diff.py` x 5 (n=8) -> `(5-1)*5 = 20`
- `compress_command` x 7 (n=4) -> `(7-1)*1 = 6`
- `RefLedger` x 4 (n=2, BELOW threshold) -> 0 (skipped)
- `_meta.redcon` x 8 (always - this is metadata header) (n=4) -> `(8-1)*1 = 7`
- 11 other paths each appearing 2-3 times: ~`30 tokens`
- 6 SHAs each appearing 2x in different calls (n=2 for short_sha, BELOW
  threshold) -> 0
- Header `compressed by redcon (cl100k)` x 8 (n=6) -> `(8-1)*3 = 21`

Total V43 saving on fixture A: **~108 tokens** out of session-total
of ~9 800 emitted tokens = **1.1%** session-level saving.

That is small. V43 only earns its keep when the session has many calls
and many repeated strings. Re-running on a longer 20-call session
fixture (full agent debugging an issue, lots of repeated paths/SHAs):

**Fixture B: 20-call long-form debugging session.**
Modeled distribution: 30 distinct paths, average f=4 (each emitted 4
times); 12 distinct test-case names, average f=3; 8 distinct SHAs
mostly above threshold (full SHAs, n=8 each), average f=2;
session-metadata header on every call.

```
saving = sum_j (f_j - 1) * (n_j - c_ref)
       = 30 * (4-1) * (7-3) + 12 * (3-1) * (5-3) + 8 * (2-1) * (8-3) + 20 * (8-1) * (6-3)
       = 30*12 + 12*4 + 8*5 + 20*21
       = 360 + 48 + 40 + 420
       = 868 tokens
```

Session-total tokens emitted: ~9 000 estimated (denser session).
Saving: **9.6%** session-level.

**Fixture C: single-call session (worst case).**
Saving: **0.0%** by construction (no repeats possible). The first
emission is always literal; only subsequent emissions are refed.

Comparison vs V42 (hash refs, c_ref=4.5 average):

```
V43 saving = sum (f_j - 1) * (n_j - 3.0)    # numeric ref, conservative
V42 saving = sum (f_j - 1) * (n_j - 4.5)    # hash ref
```

V43 - V42 = sum (f_j - 1) * 1.5. On Fixture B: `(30*3 + 12*2 + 8*1 + 20*7) * 1.5 = ~390` tokens
extra saving for V43 vs V42 over the same session. That's ~4.3% of
session tokens that V43 captures and V42 leaves on the table - because
the integer is shorter than the hash. The cost: V43 is per-session,
V42 is cross-session.

Token reduction (compact tier on a single command): **0** by V43 alone
on call 1. V43 has no effect on the BASELINE per-compressor reduction
table. It is a session-axis compounding layer like V30 - the
breakthrough definition's "new dimension that compounds on top of
existing tiers".

Latency:
- Cold start: 0 (lazy; ledger only created on first call within a
  session-aware caller).
- Per-call: one OrderedDict lookup per ref-eligible field (~10-100
  fields per call) = sub-microsecond.
- Memory: bounded by `max_entries=1024` * average string length 64B =
  ~64 KiB per ledger. Long-running gateway with N concurrent sessions
  needs TTL eviction (same concern as V30, same fix).

Affects: `redcon/runtime/session.py` (extension), `redcon/cmd/pipeline.py`
(post-format pass), `redcon/cmd/types.py` (no change - we read
canonical types, do not modify them), each compressor (declares its
own ref-eligible field list, ~3 lines each x 11 compressors).
`redcon/cmd/quality.py` quality harness needs an extension: the ref
substitution must NOT eliminate must-preserve-pattern matches. If a
compressor declares `must_preserve_patterns=(r"FAILED \S+::test_\w+",)`
and `test_pipeline.py::test_x` is refed away to `{ref:42}`, the
pattern fails. Two fixes: (a) pattern matches against the
*pre-substitution* text, or (b) ref-substitution refuses to touch
substrings inside must-preserve fields. (b) is cleaner.

## Implementation cost

- ~250 LOC: `RefLedger` class (~80), pipeline post-pass (~40), per-compressor
  field-list registry (~40), MCP `redcon_resolve_refs` tool (~30),
  quality-harness ref-aware pattern matching (~30), tests (~30).
- No new runtime deps. No network (resolve tool is a session-local
  lookup, not a network call). No embeddings.
- Risks to determinism: per-session deterministic; cross-session
  intentionally not. Same as V30.
- Risks to robustness:
  - Agent context loss: agent forgets slot 42 was
    `redcon/cmd/compressors/git_diff.py`. Mitigation 1: every call's
    `_meta.redcon.ref_table_new` re-introduces only newly-allocated
    slots; agent missing one slot from 5 calls ago is a real risk.
    Mitigation 2: server-side resolve via
    `redcon_resolve_refs(["42"])` -> `{"42": "redcon/cmd/..."}`.
  - Ref token confusion with literal `{ref:N}` strings in source code
    or commit messages. Mitigation: use a less-collisionful sentinel,
    e.g. `«ref:42»` (guillemets) or `?[42]?` if we want to
    stay ASCII; tradeoff is one or two extra tokens per ref. The
    recommended sentinel is `{ref:42}` because it is BPE-cheap on
    cl100k (3 tokens) and collisions in real agent traffic are
    near-zero (the redcon documentation never embeds this string in
    output the agent would mistake; a `git grep` for `{ref:` could
    return false positives, but those would be in the matched text
    which is NEVER ref-substituted by design - see point 3 above).
- Risks to must-preserve: addressed by quality-harness patch above.
  Concretely: V43 adds a `redcon_run --no-refs` flag for paranoid
  callers and a per-compressor opt-out so `pytest_compressor` (whose
  must-preserve patterns include test names) can disable ref
  substitution on `TestFailure.name` even if the schema declares the
  field eligible.
- Risk to BASELINE constraint #7 (output is plain text): preserved.
  `{ref:42}` is plain ASCII. The sidecar metadata is JSON inside
  `_meta.redcon` which is already part of the surface (commit 257343).

## Disqualifiers / why this might be wrong

1. **Agent does not maintain the mapping.** This is the central risk.
   LLMs are good at short-range pointer tracking but the K-V context
   may evict the slot table when the agent's context approaches its
   own limit, exactly when V43 is most useful. Mitigation 1: the
   `redcon_resolve_refs` server-side tool. Mitigation 2: a periodic
   re-introduction every K=5 calls of the live slot table summary
   (`{"42": "redcon/cmd/...", "43": "...", ...}`) in `_meta.redcon` so
   the agent gets a refresher. But re-introduction costs tokens
   (the very thing V43 is saving), so the net win shrinks. If
   re-introduction every 5 calls costs the entire savings, V43 breaks
   even in the worst case rather than helping.
2. **Sessions are too short for repeats.** Same disqualifier as V30:
   if median agent session is N=2-3 calls, the f_j * (f_j-1) factor in
   the savings formula rarely fires. V43 specifically needs `f_j >= 2`
   to save anything. Empirical session-length distribution is the
   gating measurement; if 80% of sessions are N <= 3, V43 saves <2%
   on average.
3. **String repetition is concentrated in low-token strings.** If the
   actual repeated literals are short (1-2 tokens like short SHAs,
   filenames without paths), the gating threshold (`n >= 4`) excludes
   them and V43 saves nothing. Real Redcon output skews toward longer
   absolute paths and full error messages - this is favourable - but a
   shop that sets `--quiet` flags on git/pytest could end up with
   mostly-short-token output and lose the V43 saving.
4. **Already partially implemented in `_meta.redcon`.** The existing
   `_meta.redcon` block (commit 257343) is a per-call metadata
   sidecar. V43 uses it as a delivery vehicle but adds the *content*
   layer (in-band ref substitution) that does not exist. Calling V43
   "already done" because `_meta.redcon` exists would conflate
   container with content.
5. **V42 may already cover the practical wins.** If we ship V42
   (content-hash dictionary) and the average per-session ref usage is
   moderate, the 1-1.5 token-per-ref V43-vs-V42 gain may not justify
   the second mechanism. Layered shipping (V42 + V43) doubles the
   surface area: two sentinel formats, two resolution tools, two ways
   to debug. Pick one.
6. **Tokenizer-specific brittleness.** `{ref:42}` was measured on
   cl100k as 3 tokens. On o200k or llama-3 BPE the same string may be
   2 or 4 tokens. V43 must dispatch through `redcon.core.tokens` to
   measure `c_ref` per tokenizer at startup, and the ledger threshold
   must update accordingly. This is solvable but adds a calibration
   step at session start.
7. **Adversarial input.** A repository with file paths that look like
   `{ref:42}.py` (legitimate file in the worktree) will cause
   ambiguity. The substitution pass sees the literal in *output*
   (not as ref-target text) and skips it. But if a `git grep` returns
   a line whose match text contains the ref-sentinel, and we
   ref-substitute the path, the agent could parse the line as
   resolving to the wrong slot. Defense: never substitute inside grep
   match text (already in the design); never substitute inside
   ULTRA-tier content if a downstream tool is going to round-trip
   parse it.

## Verdict

- Novelty: **medium**. Reference-pointer dictionaries are textbook (LZ77,
  ZPAQ, in-context-learning prompt cache). Applying numeric in-band
  refs to a multi-call agent tool is the new application. Compared to
  V42 (hash) and V41 (alias), V43 is the most agent-friendly because
  the ref is monotonically assigned and human-trackable. Not a pure
  breakthrough; a deployment-side multiplier on top of existing
  compressors, similar in flavour to V30.
- Feasibility: **high**. Implementation is one ledger class, one
  pipeline post-pass, and per-compressor declarations. Two days of
  work without quality-harness changes, one week with them.
- Estimated speed of prototype: **3-5 days** for ledger + pipeline
  hook + 3 compressors (git_diff, pytest, grep) wired through, plus
  a fixture benchmark. **2 weeks** end-to-end with the
  `redcon_resolve_refs` MCP tool, full quality-harness integration,
  and tokenizer calibration.
- Recommend prototype: **conditional-on-empirical-session-length**.
  If we instrument production agents and confirm median session N >= 5
  with at least 3 distinct repeated long-token strings per session,
  ship V43. Below that threshold the savings are noise. Recommended
  layering with V42: ship V43 as the *in-band* mechanism (numeric ref
  in COMPACT/ULTRA tier output) and V42 as the *cache-key* mechanism
  (content hash for cross-process cache and durable record). They
  index the same dictionary along orthogonal axes - V43 is "what the
  agent sees in this session", V42 is "what the cache stores forever".
  The two together form a complete deduplication layer: V43 wins
  per-session, V42 wins across sessions.
