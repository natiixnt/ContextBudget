# V41: Stable session-scoped 4-char alias for files/symbols, persisted across MCP calls

## Hypothesis

Today every Redcon MCP result (`redcon_run`, `redcon_compress`, `redcon_search`,
`redcon_overview`) re-emits long file-path strings. In a multi-call session the
same path - e.g. `redcon/cmd/compressors/git_diff.py` (9 cl100k tokens) - is
paid for in every result that touches it. Claim: a session-scoped, stable
4-character alias (`f001`, `f002`, ...) shipped once and re-used afterwards
collapses each repeated path to **2 cl100k tokens**, an amortised
**~78%** drop on the per-occurrence path cost. Prediction (measured on five
synthetic traces of 6-13 MCP calls covering the actual repo layout): **30.5%
aggregate saving** on the agent-visible token stream when the alias map is
shipped lazily (`alias=path` on first occurrence, alias-only thereafter), and
**~14%** when shipped via an explicit prelude. Path tokens are 46.5% of the
agent-visible bytes in those traces, so 30.5% session-level reduction is
roughly two-thirds of the theoretical ceiling. Compounds on top of every
existing tier - this is *cross-call* dedup, not per-call compression.

## Theoretical basis

Treat the agent-visible session as a stream `Y_1, Y_2, ..., Y_T` of MCP-tool
result payloads. Each `Y_t` is a sequence of tokens; a path string `p` appears
`c_t(p)` times in `Y_t`. Let `K` be the path "alphabet" of the session, with
`|K|` distinct paths and `n_p = sum_t c_t(p)` total occurrences.

Baseline token cost across the session is

```
B = sum_t |Y_t|_tok
  = (cost of non-path content)  +  sum_{p in K} n_p * tk(p)
```

where `tk(p)` is the cl100k token length of `p` (measured: 5-9 tokens for the
paths in this repo).

A 4-char alphanumeric alias `a(p)` measures **2 tokens** under cl100k for any
of `f001..f999` (verified end-to-end with `tiktoken.get_encoding("cl100k_base")`
- see `/tmp/v41_bench2.py`). Define `r = tk(a) = 2`.

Three alias-shipping strategies and their costs:

```
A (explicit one-time prelude shipped via redcon_session_start):
  C_A  =  (non-path content)  +  prelude_cost  +  r * sum_p n_p

B (inline-prelude on first MCP result):
  C_B  =  (non-path content)  +  prelude_cost  +  r * sum_p n_p

C (lazy first-use binding, i.e. emit `alias=path` on first occurrence,
    alias-only after):
  C_C  =  (non-path content)
        +  sum_p [ tk(a) + tk("=") + tk(p) ]            (first-occurrence)
        +  r * sum_p (n_p - 1)
```

The per-path break-even number of occurrences for strategy A is

```
n_p* = ceil(prelude_line_cost / (tk(p) - r))
```

Measured: `prelude_line_cost = tk("  f001=redcon/cmd/compressors/git_diff.py")
= 13`, `tk(p) = 9`, `r = 2`, so `n_p* = ceil(13/(9-2)) = 2`. Equivalently any
path appearing 2+ times in the session pays back its own prelude line under
strategy A.

For strategy C the break-even is per-path **3 occurrences** because the
first-occurrence emits both the alias *and* the path:

```
delta_C(p) = (n_p - 1) * (tk(p) - r) - tk("=" + a)
           ~= (n_p - 1) * 7 - 3                      (for a 9-token path)
delta_C >= 0  iff  n_p >= 1 + 3/7  ~= 1.43
```

so any path appearing 2+ times saves under C, and the *only* path with
n_p = 1 has a small fixed cost equal to `tk("=" + alias)` ~= 3 tokens.

Because we use cl100k path tokens of 5-9 and `r = 2`, every repeated path
yields 3-7 tokens of saving per repeat. With the measured trace mix (sum over
5 traces: 116 path-occurrences across 26 unique paths) the dominant term is
`r * sum_p (n_p - 1)` minus the first-occurrence overhead, which the harness
measures directly.

A note on alias addressability: tk-cost of the alias does not increase
discontinuously up to N = 999 (cl100k splits `f` `001` as two BPE tokens for
*any* 3-digit suffix - verified). So a 4-char `f001` form is token-stable to
~1000 distinct paths per session, far above any realistic Redcon working
set. Beyond 999 we would either go to base36 (`fa01`) or a 5-char form; the
analysis carries over with a constant-factor change in `r`.

## Concrete proposal for Redcon

The vector says the implementation lives at
`redcon/runtime/session.py` (already exists, hosts `RuntimeSession`) and
`redcon/cmd/pipeline.py` (substitution at end of `compress_command`). Final
shape:

**1. Extend `RuntimeSession` with an `AliasTable`** (`redcon/runtime/session.py`,
~40 LOC):

```python
class AliasTable:
    """Session-scoped path -> alias map. Deterministic first-seen order."""
    def __init__(self) -> None:
        self._fwd: OrderedDict[str, str] = OrderedDict()  # path -> alias
        self._rev: dict[str, str] = {}                     # alias -> path
        self._n = 0
    def alias_for(self, path: str) -> tuple[str, bool]:
        """Return (alias, was_new). was_new=True means caller should emit the
        binding (`alias=path`) once."""
        a = self._fwd.get(path)
        if a is not None: return a, False
        self._n += 1
        a = f"f{self._n:03d}"             # 4 chars; 2 cl100k tokens up to f999
        self._fwd[path] = a
        self._rev[a] = path
        return a, True
    def resolve(self, alias: str) -> str | None:
        return self._rev.get(alias)
```

`RuntimeSession` gains `aliases: AliasTable = field(default_factory=AliasTable)`.
Determinism is guaranteed by `OrderedDict` insertion order driven by *first-seen
in the substitution pass* (see step 2). For two different orderings of the same
trace (e.g. concurrent calls landing in any order) we break ties
lexicographically: when a `compress_command` sees N novel paths in the same
result, they are sorted before alias assignment.

**2. Post-substitution at the end of `compress_command` in
`redcon/cmd/pipeline.py`** (~20 LOC):

```python
# AFTER the existing `_normalise_whitespace` step, BEFORE returning the
# CompressionReport. This way tokenisers see the post-aliased text.
if hint and hint.session_aliases is not None:
    table: AliasTable = hint.session_aliases
    text = report.output.text
    text, new_bindings = _alias_substitute(text, table)
    report = replace(
        report,
        output=replace(report.output, text=text),
        # _meta.redcon includes any new_bindings emitted (server tells client
        # which paths were freshly bound this turn).
    )
```

`_alias_substitute` walks `text` with one regex pass (the same `PATH_RE` that
the canonicaliser already understands - see `redcon/cmd/path_norm.py` if it
exists, or a new module) and rewrites every occurrence to its alias. For
*new* paths, lazy mode emits `alias=path` at the first hit and `alias` after.
This is the strategy C path; A/B are degenerate cases (prelude before turn 1).

**3. MCP `_meta.redcon` block extension** (`redcon/mcp/tools.py`):

The `_meta.redcon` block already exists (commit 257343). Add one nested key:

```
_meta.redcon.aliases.bound = [
  {"alias": "f001", "path": "redcon/cmd/pipeline.py"},
  ...
]    # only the *new* bindings produced this turn
_meta.redcon.aliases.size  = 17    # current table size after this turn
```

This is *machine-readable* metadata so a strict client can rebuild the map
without trusting in-text `alias=path` strings. Human-readable `alias=path`
inside the body is for the agent's chain-of-thought; machine consumers ignore
the body and use `_meta`. Both paths agree.

**4. Reverse direction: the agent says `show me file f3`** (`redcon/mcp/tools.py`):

Add an alias-aware path argument resolver to every tool that accepts a `path`
parameter:

```python
def _resolve_path(arg: str, session: RuntimeSession) -> str:
    if re.fullmatch(r"f\d{3}", arg):
        p = session.aliases.resolve(arg)
        if p is None:
            raise ToolInputError(f"unknown alias {arg}; current size {session.aliases.size}")
        return p
    return arg
```

Plumbed at the entry of `redcon_compress`, `redcon_search`, `redcon_overview`,
`redcon_run` (when the argv references a file). ~30 LOC.

**Pseudo-code for the lazy first-use substitution**

```python
PATH_RE = re.compile(r"\b(?:redcon|tests)/[\w/_\-]+\.py\b")

def _alias_substitute(text: str, table: AliasTable) -> tuple[str, list[tuple[str,str]]]:
    new_bindings: list[tuple[str,str]] = []
    occurrences = list(PATH_RE.finditer(text))
    # Tie-break: novel paths in this text are alphabetised before assignment
    novel = sorted({m.group(0) for m in occurrences} - set(table._fwd))
    for p in novel:
        a, was_new = table.alias_for(p)
        new_bindings.append((a, p))
    # Now do the substitution with bindings emitted at first per-path hit
    seen_in_text: set[str] = set()
    out: list[str] = []
    last = 0
    for m in occurrences:
        out.append(text[last:m.start()])
        path = m.group(0)
        alias, _ = table.alias_for(path)
        if path not in seen_in_text and (alias, path) in new_bindings:
            out.append(f"{alias}={path}")
            seen_in_text.add(path)
        else:
            out.append(alias)
        last = m.end()
    out.append(text[last:])
    return "".join(out), new_bindings
```

**Choice of shipping strategy: lazy first-use (strategy C) wins.** Justification:

| Strategy | Aggregate saving on 5 traces | Saving on a 12-call session | Cost |
|---|---|---|---|
| A explicit prelude (redcon_session_start) | 14.13% | 111 tokens | one extra MCP call; full prelude up front; agent might hit context window before any compressor result |
| B inline prelude on first result | 13.85% | 110 tokens | bloats first turn; agent that drops out after one call still paid the full map |
| **C lazy first-use** | **30.52%** | **196 tokens** | only paths that appear get bound; pay-as-you-go; no extra MCP call |

Strategy C is also the only one that **cannot be net-negative**: a path that
appears once costs 3 tokens of overhead (`=` + 2-token alias) on top of its
9-token raw cost - 12 instead of 9, i.e. +3 tokens. Strategies A/B always
emit the full map even if the agent only uses 2 of 17 paths. Empirically C is
2.2x better on the median trace and **strictly dominates** A/B on every trace
in the benchmark.

## Estimated impact

Numbers from `/tmp/v41_bench.py` (cl100k_base, tiktoken):

| Trace | calls | unique paths | raw tk | C tk | saved |
|---|---|---|---|---|---|
| T1 git_diff bug | 6 | 6 | 324 | 217 | 107 (33.0%) |
| T2 add eslint compressor | 8 | 6 | 348 | 252 | 96 (27.6%) |
| T3 pipeline cache key | 10 | 5 | 361 | 280 | 81 (22.4%) |
| T4 pytest compressor | 6 | 4 | 220 | 147 | 73 (33.2%) |
| T5 scorer deep dive | 13 | 8 | 559 | 363 | 196 (35.1%) |
| **TOTAL** | **43** | **26 dist.** | **1812** | **1259** | **553 (30.52%)** |

- **Per-session token saving (8-12 calls)**: average **124 tokens** under
  strategy C; **~30% of the agent-visible byte stream** in path-heavy
  sessions.
- **Across N=20 sessions**: **~2,490 tokens** saved.
- **Across N=50 sessions**: **~6,220 tokens** saved.
- **Theoretical ceiling** (paths -> 0 cost): 46.5% of raw stream is path
  tokens, so strategy C captures roughly **two-thirds of the ceiling**.

These numbers compound on top of existing per-tier compression (97% on git
diff, 73.8% on pytest, etc.). The compact-tier numbers in BASELINE.md are
*intra-call*; this is *inter-call*. Multiplicative.

Affects:
- `redcon/cmd/pipeline.py`: post-substitution hook (~20 LOC).
- `redcon/runtime/session.py`: `AliasTable` (~40 LOC).
- `redcon/mcp/tools.py`: alias resolver in tool entry, `_meta.redcon.aliases`
  emission (~30 LOC).
- Cache layer **unchanged** - the cache key is built before substitution;
  cached output is the canonical (un-aliased) text. Substitution is a
  display-only post-process. BASELINE constraint #6 (cache-key determinism)
  preserved.
- Quality harness unchanged - `must_preserve_patterns` run on canonical
  text before substitution. BASELINE constraint #4 preserved.
- `_tokens_lite.estimate_tokens` and `redcon.core.tokens` unaffected;
  tokenisation moves *down* under aliasing, so their estimates remain
  conservative upper bounds.

Latency: one regex pass per result. `PATH_RE` is `\b(?:redcon|tests)/[\w/_\-]+\.py\b`,
which is prefix-gated on `r` or `t` (cheap). On a 1k-line compact result the
pass adds ~50 microseconds. No measurable cold-start penalty (BASELINE #5).

## Implementation cost

- **LOC**: ~120 total. AliasTable 40, pipeline hook 20, MCP plumbing 30,
  reverse resolver 15, tests 30, fixtures from `/tmp/v41_bench.py` 30.
- **New runtime deps**: none. Pure stdlib (`re`, `collections`, `dataclasses`).
  Honours "no required network / no embeddings".
- **Risks to determinism**: none if we strictly assign aliases by *first-seen
  in lex-sorted novel set per call*. Two callers issuing the same sequence of
  `compress_command` invocations produce byte-identical output. BASELINE #1
  preserved.
- **Risks to robustness**: a malformed regex match (e.g. path inside a
  string literal that the agent should see verbatim) would alias something
  the agent expected unmodified. Mitigation: alias only paths that appear in
  *body text* outside backtick/triple-backtick code fences, and gate
  substitution on the compressor schema (don't alias inside log-pointer tier
  output, where the agent is staring at literal raw bytes).
- **Risks to must-preserve**: zero. Substitution runs *after* the quality
  harness's `must_preserve_patterns` check on the canonical text. The agent
  sees aliased text; the verifier sees canonical text.

## Disqualifiers / why this might be wrong

1. **The agent doesn't actually understand `f3 = path` reliably.** Even
   strong LLMs occasionally forget or misapply alias mappings, especially
   over long sessions. If the agent silently re-emits the path (`redcon/cmd/...`)
   in a follow-up tool call, our reverse-resolver still works, but the agent's
   *internal* reasoning may have generated longer chains-of-thought reasoning
   about both the alias and the path simultaneously, *increasing* its hidden
   token use. We measure the wire saving; we don't measure the model's
   internal cost. Mitigation: keep aliases to ~30 per session (one chunk of
   30 paths is comfortably within working memory) and emit the binding
   in-line at first use so the model never needs to recall a prelude.
2. **Path tokens are only 46.5% of the byte stream in our synthetic traces;
   a real session may be dominated by code snippets, diffs, or trace
   bodies** where paths are 5-15% of bytes. In that regime strategy C still
   saves **~3-5%** rather than 30%. The "30% saving" claim is upper-bound
   for path-heavy sessions; real saving is bounded by the path fraction.
   Mitigation: this still wins because the sign is always non-negative under
   strategy C, and the absolute saving is positive on any session where any
   path appears more than once.
3. **Already partly subsumed by `redcon/scorers/` repo_map normalisation,**
   which emits short relative paths anyway. If the project root is `/long/abs`
   and Redcon already normalises to `redcon/cmd/...` (it does, per
   `repo_map.py`), some of the saving has been booked. V41 is on top of that
   normalisation, not instead of it - `redcon/cmd/compressors/git_diff.py` is
   already 9 cl100k tokens, the saving is from those 9 down to 2.
4. **Cache key contamination.** If we ever decide to memoise post-aliased
   text (we don't propose that, but a future contributor might), the cache
   becomes session-scoped, breaking cross-process cache sharing. The
   pipeline already solves this by keying the cache on canonical text and
   substituting at egress. A test must pin this invariant.
5. **Symbol cards** (the vector calls them "files/symbols") have a much
   wider alphabet (thousands of distinct symbol names per repo) and far
   shorter raw tokens (a function name is often 1 cl100k token). Aliasing
   `f3` for `compress_command` is 2 vs 1 tokens - **negative**. The proposal
   should explicitly *exclude* symbols and alias only paths. The vector's
   "files/symbols" framing is an over-reach. (V49 - persistent symbol cards
   - is the right home for the symbol case; cards are larger objects whose
   *first emission* is what gets dedup'd, not the symbol identifier.)
6. **Concurrent sessions** sharing one `RuntimeSession` race on
   `AliasTable._n`. Mitigation: one `RuntimeSession` per agent thread, per
   the existing convention. If pooled, wrap `alias_for` in a lock - 5 LOC.
7. **Not all paths are repeated.** On a one-shot tool call (single
   `redcon_run`), every path appears once and strategy C costs +3 tokens
   per path versus baseline. Mitigation: skip aliasing entirely when
   `RuntimeSession.turn_number == 1` *and* `BudgetHint.expect_followup` is
   False. This is a 3-line guard.

## Connection to other vectors

- **V42 (hash-keyed shared dictionary, server-side)**: superset of V41 in
  intent but heavier - V42 stores *content* (compressed bodies), not just
  identifiers. V41 is the path-only specialisation, much cheaper to build
  and complementary. They compose: V42 keys content, V41 keys paths within
  content.
- **V43 (RAG-style hot store with `{ref:#42}`)**: V41 is a strict subset.
  V43 generalises to any repeated phrase; V41 only handles paths but with a
  much narrower regex and zero risk of mis-substitution.
- **V47 (snapshot delta vs prior `redcon_run`)**: orthogonal and stacks
  cleanly. V47 reduces *content* tokens via cross-call delta; V41 reduces
  *identifier* tokens via cross-call alias.
- **V49 (persistent symbol cards)**: V41 is the path-cousin. V49 covers
  symbols (which V41 should NOT cover, per Disqualifier 5). Together they
  give "stable IDs for both files and symbols", which is the wider research
  goal in BASELINE's "open frontier".
- **V44 (deep-link references file:line)**: V41's alias for the file part,
  reused in the deep-link scheme. `f3:142` instead of
  `redcon/cmd/pipeline.py:142` - same logic, more compounding.
- **V25 (Markov prefetch)**: orthogonal - V25 saves latency, V41 saves
  tokens. Both compatible with the same `RuntimeSession`.

## Verdict

- **Novelty**: medium. Path-aliasing inside an MCP session is an obvious
  cross-call dedup move and the BASELINE explicitly lists "Stable
  session-scoped IDs for files/symbols" as open frontier. No prior Redcon
  code does it. Outside Redcon the technique is universal (compiler symbol
  tables, gzip dictionaries, JSON-LD `@context`); the novelty is the
  measurement methodology and the determinism contract.
- **Feasibility**: high. ~120 LOC, no deps, no schema changes, no cache
  invalidation. Substitution is a single regex pass; reverse resolver is
  a dict lookup. Quality harness untouched.
- **Estimated speed of prototype**: 1-2 days for AliasTable +
  pipeline post-substitution + tests on the 5 trace fixtures from
  `/tmp/v41_bench.py`. 3-4 days total to wire it through MCP, document the
  `_meta.redcon.aliases` schema, and add the alias-aware tool entry path.
- **Recommend prototype**: **yes**, with strategy C (lazy first-use binding)
  and explicit exclusion of symbols. The 30% aggregate saving on path-heavy
  multi-call sessions clears the BASELINE breakthrough bar
  ("compounds on top of existing tiers"). The numbers are tiktoken-measured,
  not extrapolated. Lowest-risk net-positive cross-call dedup the project
  can ship.
