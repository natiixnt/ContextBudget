# V11: Provenance-aware patch DAG with shared-subgraph contraction across hunks of multi-file diff

## Hypothesis

A multi-file commit often contains the same edit applied to many files
(rename, signature change, mass import swap). Today
`redcon/cmd/compressors/git_diff.py` walks each `(file, hunk)` pair
independently and, at VERBOSE tier, emits up to 5 added + 5 removed lines
per hunk. The claim: model the diff as a DAG whose nodes are
`(file, hunk_idx)` and whose edges are "shares more than K identical
add/remove tokens". Contract every connected subgraph with `|V| >= 3` into
one representative body plus an N-line "applied at" footer.

Concrete prediction: on real Redcon commits, this reduces VERBOSE-tier
hunk-body tokens by roughly 4-5 % on average and by up to ~11 % on
refactor commits (mass import rewrites, rename-driven changes). It does
**not** help COMPACT or ULTRA, which already drop hunk bodies and reach
97 % reduction (BASELINE.md line 18). The win is therefore confined to
the VERBOSE tier of `git_diff` and is best framed as "make VERBOSE
sensible on big refactor diffs" rather than a compact-tier breakthrough.

## Theoretical basis

### Setup

A unified diff with `H` hunks can be modelled as a graph
`G = (V, E)`, `V = {h_1, ..., h_H}`, where each `h_i` carries an
add/remove token shingle multiset `S_i`. Define edge weight
`w(h_i, h_j) = J(S_i, S_j)` (Jaccard similarity). For a threshold
`tau`, the contracted graph `G_tau` keeps only edges with `w >= tau`,
and every connected component `C subset V` becomes a *cluster*.

### Token-cost model

Let the verbose-tier emission cost for hunk `i` be
`b_i = estimate_tokens(raw_hunk_text_i)`. Without contraction, the
total cost is

```
  T_naive = sum_{i in V} b_i.
```

With contraction, every cluster `C` with `|C| >= n_min` is replaced by
one representative body of cost `b_rep = max_{i in C} b_i` (or any
member; bodies in a high-Jaccard cluster have similar size by
construction) plus `|C|` short footer lines of cost
`f_i = estimate_tokens(f"- {file_i}@{line_i}")`. Hunks outside any
qualifying cluster pay their full `b_i`. The contracted total is

```
  T_dag = sum_{C: |C|>=n_min} ( b_rep(C) + sum_{i in C} f_i )
        + sum_{i not in any qualifying C} b_i.
```

The saving is therefore

```
  delta = T_naive - T_dag
        = sum_{C: |C|>=n_min} ( sum_{i in C} b_i - b_rep(C) - sum_{i in C} f_i )
        ~= sum_{C: |C|>=n_min} ( (|C| - 1) * b_avg(C) - |C| * f_avg ).
```

For the win to be positive, each qualifying cluster must satisfy

```
  b_avg(C) > ( |C| / (|C| - 1) ) * f_avg.
```

For `|C| = 3` and `f_avg ~= 6` cl100k tokens (a path-and-line
reference), that requires `b_avg > 9` tokens, i.e. any cluster of >=3
hunks whose bodies average more than ~9 tokens shrinks. Empirically
all observed clusters satisfy this by 1-2 orders of magnitude.

### Why Jaccard on token shingles

Hunks differ in indentation, surrounding context lines, and exact
line-number metadata. Set-of-shingles ignores all three. The
representation is byte-stable (shingles are sorted), which preserves
the determinism constraint (BASELINE.md constraint 1).

### Measured numbers

99 commits surveyed (`git log --no-merges -100`), tau = 0.8, Jaccard
threshold over 3-token shingles built from add+remove bodies.

| Stat | Value |
|---|---|
| Commits scanned | 99 |
| Commits with >=2 files | 60 (60.6 %) |
| Commits with any cluster of size >=2 | 21 (21.2 %) |
| Commits with any cluster of size >=3 | **14 (14.1 %)** |
| Total hunk-body tokens across all commits | 358 110 |
| Total tokens saved by V11 | 16 272 |
| Population-level avg saving as % of hunk-body tokens | **4.54 %** |

Per-commit detail on the 5 explicitly chosen commits (tau = 0.8, n_min = 3):

| Commit | Files | Hunks | Cluster of size >=3 | Tokens saved | % of hunk-body tokens |
|---|---:|---:|---:|---:|---:|
| 2cc0253 (100 improvements refactor) | 24 | 148 | 5 clusters covering 16 hunks | 885 | 2.69 % |
| d52c387 (lazy-import refactor) | 12 | 12 | 1 cluster covering 9 hunks | **463** | **11.08 %** |
| 50d2a95 (rewriter feat) | 6 | 19 | 0 | 0 | 0.00 % |
| a44993b (multi-compressor add) | 8 | 13 | 0 | 0 | 0.00 % |
| a56aa74 (format perf) | 5 | 16 | 0 | 0 | 0.00 % |

The d52c387 cluster is the "smoking-gun" case: 9 files all carry the
identical 1-line replacement
`-from redcon.core.tokens import estimate_tokens` ->
`+from redcon.cmd._tokens_lite import estimate_tokens`. Each of the 9
hunks costs ~80 cl100k tokens (header + 3 context lines + +/- pair).
Emitting the body once and listing 9 file paths cuts 463 of 4 180
total hunk-body tokens.

### Threshold sensitivity

| tau | commits with >=3-cluster | total saved tokens |
|---:|---:|---:|
| 0.6 | higher coverage but risks merging unrelated edits | 2 130 on the 2cc0253 commit alone |
| 0.7 | 1 526 on 2cc0253 | - |
| 0.8 (recommended) | 14 / 99 commits | 16 272 |
| 0.9 | identical to 0.8 in this corpus (cluster bodies are near-identical) | - |

tau = 0.8 is the knee: looser misses 2cc0253-style "same pattern,
slightly different formatting" but risks merging unrelated rename
hunks; tighter loses nothing on this corpus.

## Concrete proposal for Redcon

### File: `redcon/cmd/compressors/git_diff.py`

Add a new internal format mode `compact-shared` invoked when
`level == VERBOSE` **and** `len(result.files) >= 2` **and** total hunks
>= 3. The mode runs hunk clustering before formatting; if no qualifying
cluster is found, falls through to the existing `_format_verbose`
output (byte-identical, so cache keys do not split).

```python
# new, ~80 lines, lives next to _format_verbose
def _format_verbose_shared(result: DiffResult) -> str:
    flat = [(f.path, hi, h) for f in result.files
                            for hi, h in enumerate(f.hunks)]
    if len(flat) < 3:
        return _format_verbose(result)
    sigs = [_shingle(h) for _, _, h in flat]
    clusters = _greedy_cluster(sigs, threshold=0.8)
    qualifying = {i: cid for cid, ix in enumerate(clusters)
                          if len(ix) >= 3 for i in ix}
    if not qualifying:
        return _format_verbose(result)
    lines = [f"diff --git summary: {len(result.files)} files, "
             f"+{result.total_insertions} -{result.total_deletions}", ""]
    emitted_clusters: set[int] = set()
    for f in result.files:
        lines.append(_file_header(f))
        for hi, h in enumerate(f.hunks):
            flat_idx = _flat_index(flat, f.path, hi)
            cid = qualifying.get(flat_idx)
            if cid is None:
                lines.extend(_format_hunk(h))
                continue
            if cid not in emitted_clusters:
                lines.append(f"# shared edit, applied at "
                             f"{len(clusters[cid])} locations:")
                lines.extend(_format_hunk(h))
                emitted_clusters.add(cid)
            else:
                lines.append(f"  - {f.path}@{h.new_start} (same as above)")
    return "\n".join(lines)
```

Helpers (`_shingle`, `_greedy_cluster`, `_flat_index`, `_file_header`,
`_format_hunk`) are pure, deterministic, and only run on hunks that
already passed the prefix-gate parser. No new runtime deps.

### File: `redcon/cmd/types.py`

No schema change. The `DiffResult` -> string contract is unchanged;
`compact-shared` is purely an internal formatter chosen inside
`_format`.

### Cache key

No change. Argv canonicalisation already produces a stable key; the
new formatter is a deterministic function of `result + level`, so
identical `(argv, cwd)` always yields identical output. Constraint 6
preserved.

### must_preserve_patterns

The current pattern matches `^[A-Z] [^\s]+|^- ?[^\s]+`, which is what
the cluster-footer (`  - path/foo.py@123`) matches. So the verifier
keeps passing. The shared-edit body is one of the original hunks
verbatim, so any token that previously survived a single hunk emission
still survives.

## Estimated impact

- **Token reduction (VERBOSE git_diff only)**:
  - Population mean: **-4.5 % of hunk-body tokens** (16 k of 358 k
    across last 99 commits).
  - Best case (lazy-import-style refactor commits): **-11 %**.
  - Effect on the COMPACT tier: **0 %** (already drops bodies; the
    97 % reduction in the baseline table is unaffected and unrelated).
  - Effect on ULTRA: 0 %.
- **Latency**: clustering is `O(H^2 * |S|)` Jaccard. For typical
  H <= 50 and shingle size ~30, this is well under 1 ms; for H = 148
  (commit 2cc0253) it is ~5 ms. Cold-start unaffected (lazy import
  the cluster module). Constraint 5 preserved.
- **Affects**: `redcon/cmd/compressors/git_diff.py` only. Other
  compressors, scorers, cache layers are untouched.
- **Quality harness**: must-preserve still holds (see above). The
  reduction-floor check at VERBOSE is `-10 %` (BASELINE.md line 30),
  so a 4.5 % saving on hunk-body tokens never trips a regression.

## Implementation cost

- LOC: ~80 lines in `git_diff.py` for `_format_verbose_shared`,
  `_shingle`, `_greedy_cluster`. A few lines in `_format` to dispatch.
- Tests: 3 fixtures (mass-import-rewrite, rename, no-cluster) under
  `tests/cmd/compressors/test_git_diff_shared.py`.
- New runtime deps: **none**. No network. No embeddings. Constraint 2
  and 3 preserved.
- Risks:
  - Determinism: Jaccard with sorted shingles + union-find with
    deterministic iteration order is byte-stable. Order of cluster
    representatives must be the cluster's lowest flat-index hunk; this
    is enforced by iterating `flat` in document order.
  - Robustness: malformed diffs already short-circuit via the
    parser's tolerant path; cluster step skips empty shingle sets.
  - must-preserve: the representative hunk is one of the cluster
    members verbatim, so no information is invented; the footer
    repeats the file paths that the existing pattern requires.

## Disqualifiers / why this might be wrong

1. **The wrong tier**. COMPACT (the default tier in budget-pressured
   settings, the one most agents will see) already drops hunk bodies
   entirely (`_format_compact` keeps only the first hunk header and
   `+N more hunks`). V11 therefore touches an output most agents
   never see. The 97 % already-shipped reduction in BASELINE.md
   makes this a niche optimisation by construction.
2. **Long-tail distribution**. 86 % of recent commits hit no >=3
   cluster at all (14/99). The mean saving is 4.5 % of hunk-body
   tokens but the median is **0 %**. A lookbehind that runs on every
   `git diff` to find clusters that 86 % of the time do not exist
   is real CPU spent for nothing, even if cheap.
3. **Equivalent or better techniques in the index**:
   - V12 (semantic-equivalence canonical form) and V13 (CST template
     extraction) target the same redundancy at a deeper, multi-line
     layer and would subsume V11 by construction.
   - V19 (AST-diff representation) is a strictly stronger model of
     "same edit in many places" because it keys on the AST, not on
     surface tokens, and would catch the cases V11 misses (renames
     with reformatted whitespace).
   - The cross-call dictionary themes (V41-V50) get the same effect
     across calls, not just within one diff, which is a much bigger
     surface.
4. **Cluster discovery noise**. At tau = 0.6 we saw 21.6 % of hunks
   bunch into clusters but those clusters merged unrelated 2-line
   parameter changes. Picking tau = 0.8 stabilises but means a
   truly novel single-line refactor pattern that differs by one
   identifier slips out. The "right" threshold is data-dependent;
   making it commit-adaptive moves us toward a learned model and
   pulls in the no-embedding constraint debate.
5. **Hunks are pre-aggregated by file**. Git already collapses N
   identical-line edits inside one file into one hunk (or splits
   them by surrounding context). V11 only fires across files; a
   refactor confined to one file gets nothing. In our 5 hand-picked
   commits, three saw zero benefit.

## Verdict

- Novelty: **medium**. The diff-as-DAG framing is not present in
  Redcon today, but the index already lists stronger neighbours
  (V12, V13, V19) and the win sits on a tier (VERBOSE) that the
  default budget-pressure path rarely picks.
- Feasibility: **high**. Pure additive formatter, no deps, no
  schema change, ~80 LOC, deterministic.
- Estimated speed of prototype: **~4-6 hours** including fixtures
  and the quality-harness round.
- Recommend prototype: **conditional-on-X** - prototype only if
  V19 (AST-diff) and V12 (canonical form) are both judged out of
  scope. Otherwise V11 is a strict subset of either and shipping
  it first creates churn in `git_diff.py` that V19/V12 would
  rewrite. Standalone, V11 buys ~4.5 % VERBOSE-tier reduction on a
  tier most agents do not see, which is below the BASELINE
  "breakthrough" bar of >=5 pp on COMPACT across multiple
  compressors.
