# V37: Greedy multi-candidate phrasing - try N rewrites of every templated section, keep the min-token version

## Hypothesis

For each templated emit site in Redcon's command-side compressors (header line, per-row format string, "+N more" sentinel, etc.) there is a small set of phrasings that produce **byte-different but information-equivalent** output. cl100k tokenises each of these phrasings differently. If we enumerate 3-5 phrasing candidates per template, evaluate them against a small corpus of representative inputs at compressor-author time, and bake the per-template winner into the source, we capture small wins on every template that compound into a meaningful corpus-level reduction. The work is fully off-line: the production code keeps a single static format string per site, with no runtime branching.

Specifically I predict:
- ~10-30% per-template token reduction *on the rendered template's own bytes* for templates the human author phrased prose-first ("X passed, Y failed" -> "Xp Yf").
- ~3-8% reduction in the overall compact-tier output where the template is one component among many (paths/numbers/messages dominate).
- Per-output savings of **~2-30 tokens** for short outputs (status, log, pytest summary), **~80-220 tokens** for repeated-row outputs (grep, ls -R, lint).
- Subject to the must-preserve harness: a fraction of the unconstrained wins are illegal (drop required keywords like `branch:`, `commit`, `[A-Z] path`) and must be discarded, costing ~3% of the gross unconstrained saving.

## Theoretical basis

cl100k is a byte-pair-encoding (BPE) tokenizer. Two strings `s_1` and `s_2` that carry the same numeric/textual information can have `tok(s_1) != tok(s_2)`. Common reasons:
1. **Whitespace before separators.** ` files, +5 -1` tokenises differently from ` files,+5-1`. Commas tend to be merged with adjacent tokens; spaces around them are not.
2. **Decorative words.** `1 commits` (2 tok) vs `log: 1 commits` (5 tok) - the prefix `log:` adds 3 tokens that a context already conveys via the calling tool name in the agent's prompt.
3. **Singular/plural.** `commit` is one tok, `commits` is one tok (`s` merges), but `entries` (1 tok) vs `entry` (2 tok), `directories` (2 tok) vs `dirs` (1 tok).
4. **Suffix capitalisation.** `Running:3` is 3 tok (`Running`, `:`, `3`) vs `running:3` similarly. But `running 3` adds a space token boundary and merges differently. Not always cheaper either way; must be measured.
5. **Bracket choice.** `[a, b]` and `(a, b)` and `{a, b}` produce distinct token sequences because BPE has merged certain bracket+space sequences but not others.

For a template `T` with `K` candidates, run against `N` representative inputs:
```
saving(T) = ( sum_i tok(T_shipped(x_i)) ) - ( sum_i tok(T_best(x_i)) )
where T_best = argmin_{c in K} sum_i tok(c(x_i))
```
With `N = 5` samples per template and `K = 4-5` candidates, the search is exhaustive (`O(K * N)`) and trivial (~150 token-encodes per template).

Worst-case bound: a single template's saving is bounded above by `tok(T_shipped) - tok(min phrasing equivalent)`. Empirically the floor is ~0 (3 templates here are already optimal: `log.commit_row`, `common.errors_block`, `common.warnings_block`) and the cap is ~30 token total over 5 samples for `test.summary_passed_failed` (the wordiest header in the codebase).

The technique cannot break the COMPACT-tier reduction floor (30%): it strictly *improves* it because it reduces compressed token count without touching original token count, so the must-preserve harness's reduction check `1 - compressed/original >= 0.3` only gets easier.

## Concrete proposal for Redcon

This is a one-shot tuning pass, not a runtime feature. Deliverable: a script `/tmp/v37_phrasing/v37_greedy_phrasing.py` (full path of the corresponding artifact in this experiment) that enumerates templates and renders winners. The output of running the script is a list of source-file edits the maintainer applies once. Production code change: replace each `f"..."` template at the cited line with the winner.

```python
# v37_greedy_phrasing.py - sketch
TEMPLATES = [
    {
        "id": "git_diff.compact_summary",
        "site": "redcon/cmd/compressors/git_diff.py:265-266",
        "candidates": [
            ("*shipped",       lambda f, i, d: f"diff: {f} files, +{i} -{d}"),
            ("colon-no-space", lambda f, i, d: f"diff:{f} files +{i}-{d}"),
            ("delta-form",     lambda f, i, d: f"diff {f}f +{i} -{d}"),
            # ...
        ],
        "samples": [{"f": 1, "i": 3, "d": 1}, ...],
    },
    # ~28 more templates
]

for tpl in TEMPLATES:
    per_cand = []
    for label, fn in tpl["candidates"]:
        total = sum(tok(fn(**s)) for s in tpl["samples"])
        outputs = [fn(**s) for s in tpl["samples"]]
        valid = passes_must_preserve(tpl["id"], outputs)
        per_cand.append((label, total, outputs, valid))
    best = min((c for c in per_cand if c[3]), key=lambda c: c[1])
    print(f"{tpl['id']}: shipped={shipped_total} -> best={best[1]} ({best[0]})")
```

The `passes_must_preserve` check loads each compressor's `must_preserve_patterns` (declared on the class) and rejects any candidate that breaks them. This is the safety harness V37 must have to avoid the "loud silent regression" failure mode.

Production code changes are edits on existing format strings only. Example for `redcon/cmd/compressors/git_log.py:181`:
```python
# before
lines = [f"log: {len(result.entries)} commits"]
# after
lines = [f"{len(result.entries)} commits"]   # 'log:' prefix is redundant
```
No runtime cost. No new modules. No new imports. Cache key unaffected (output text changes byte-for-byte, but cache is keyed on argv+cwd so output formatting changes do not invalidate it; the next run will simply emit the new shorter form on cache miss).

## Estimated impact

### Per-template wins (cl100k_base, tiktoken)

Across **29 templates** spanning 11 compressors, with 4-5 representative inputs each, total token cost:

| Filter | Total shipped | Total best | Saved | % |
|---|---:|---:|---:|---:|
| **Pass 1** (greedy minimum, no constraints) | 1390 | 1102 | 288 | 20.7% |
| **Pass 2** (greedy + must-preserve enforced) | 1390 | 1110 | 280 | 20.1% |

The must-preserve filter rejects only **3 candidates** across the corpus (the very-aggressive ones for `status.compact_head` and `log.compact_head`), so the safety pass costs only ~8 tokens of the gross win. **26 of 29 templates** have a strictly cheaper phrasing than what is currently shipped; **3 templates** (`log.commit_row`, `common.errors_block`, `common.warnings_block`) are already optimal under cl100k.

### Top 10 winning templates (must-preserve enforced)

| Template | Site | N | Shipped | Best | Saved | Winner phrasing |
|---|---|---:|---:|---:|---:|---|
| `test.summary_passed_failed` | `test_format.py:101-111` | 5 | 102 | 72 | **30** | `pytest 96p 0f /96 5.34s` |
| `grep.summary` | `grep_compressor.py:251/259` | 5 | 46 | 26 | **20** | `grep 250/38` |
| `docker.ps_head` | `docker_compressor.py:186` | 4 | 40 | 20 | **20** | `ps:5/3` |
| `status.compact_head` | `git_status.py:177-181` | 4 | 70 | 50 | **20** | `branch:main->origin/main +0/-0` |
| `lint.by_code` | `lint_compressor.py:215` | 4 | 75 | 62 | **13** | `codes: E501=12 F401=4` |
| `log.compact_head` | `git_log.py:181` | 4 | 20 | 8 | **12** | `47 commits` (drop `log:` prefix) |
| `git_diff.ultra_summary` | `git_diff.py:253-256` | 3 | 95 | 83 | **12** | `2f +5 -0 [a.py, b.py]` |
| `listing.compact_head` | `listing_compressor.py:262` | 4 | 53 | 41 | **12** | `ls: 12f 3d/1` |
| `listing.ext_histogram` | `listing_compressor.py:265-267` | 4 | 55 | 43 | **12** | `ext py=87 md=6 txt=2` |
| `lint.summary` | `lint_compressor.py:208-211` | 4 | 56 | 44 | **12** | `mypy:0e/0w/0f` |
| `docker.build_head` | `docker_compressor.py:345-348` | 4 | 48 | 36 | **12** | `docker build {s} {n}s/{c}c` |
| `git_diff.compact_summary` | `git_diff.py:265-266` | 5 | 53 | 43 | **10** | `diff:5 files +42-17` |
| `git_diff.more_hunks` | `git_diff.py:286` | 5 | 25 | 15 | **10** | `...3h` (ellipsis + suffix) |
| `kubectl.head` | `kubectl_compressor.py:155-160` | 4 | 58 | 49 | **9** | `pods[12] Running:10...` |
| `pkg_install.head` | `pkg_install_compressor.py:239-256` | 4 | 57 | 49 | **8** | `pip +5/-2/~1 0.8s` |
| `test.fail_head` | `test_format.py:60-61` | 5 | 81 | 73 | **8** | `FAIL test_x:tests/test.py:42` (colon, not paren) |
| `listing.more_dirs` | `listing_compressor.py:273` | 4 | 20 | 12 | **8** | `+7 dirs` (drop `... ` and `directories`) |
| `status.more_entries` | `git_status.py:191` | 4 | 20 | 12 | **8** | `+30 entries` |
| `grep.line_match` | `grep_compressor.py:269` | 5 | 49 | 42 | **7** | `42:    return self.value` (no `L`) |
| `git_diff.per_file_row` | `git_diff.py:274-276` | 5 | 56 | 51 | **5** | `M path:+i-d` (no spaces around colon) |
| `grep.path_with_count` | `grep_compressor.py:262` | 5 | 40 | 35 | **5** | `path:12` |
| `lint.file_row` | `lint_compressor.py:222` | 4 | 32 | 28 | **4** | `path x12` |
| `grep.more_matches` | `grep_compressor.py:271` | 4 | 13 | 9 | **4** | `...4` |
| `test.warnings_count` | `test_format.py:68` | 4 | 16 | 12 | **4** | `warnings:3` |
| `common.more_n` | `various` | 4 | 12 | 8 | **4** | `+N` (drop `more`) |

### Per-output extrapolation

Multiplying per-fire savings by typical fire counts in a compact-tier output:

| Typical output | Fires | Saved (tok) | Approx % of compact-tier output |
|---|---:|---:|---:|
| `grep` (250 matches in 38 files) | 173 | **221.6** | ~14.2% |
| `ls -R` (30 dirs, ext histogram) | 33 | **90.5** | ~19.4% |
| `lint mypy` (47 issues / 14 files) | 16 | **20.2** | ~15.2% |
| `git_diff` (5 files) | 8 | **11.0** | ~13.3% |
| `pytest` (3 fail / 96 pass) | 4 | **10.8** | ~10.2% |
| `git status` (busy) | 2 | 7.0 | ~10% |
| `docker ps` (5 containers) | 1 | 5.0 | ~12% |
| `git_diff` ULTRA (50 files) | 1 | 4.0 | ~5% |
| `docker build` (legacy + tags) | 4 | 4.0 | ~3-4% |
| `git log` (47 commits) | 31 | 3.0 | ~1% (dominated by subjects) |
| `kubectl get pods` (12 pods) | 1 | 2.2 | ~3% |
| `pip install` (5 pkgs) | 1 | 2.0 | ~4% |
| **Sum of one of each** | **275** | **~381** | - |

Comparison to BASELINE.md compact-tier reductions:

| Compressor | Existing reduction | After V37 (estimate) | Delta |
|---|---:|---:|---:|
| git diff | 97.0% | 97.0%-97.2% | +0.0-0.2 pp |
| pytest | 73.8% | 74.5%-75.0% | +0.7-1.2 pp |
| grep / rg | 76.9% | ~80% | +~3 pp |
| find | 81.3% | ~84% | +~3 pp |
| ls -R | 33.5% | ~46% | **+~12 pp** |

The `ls -R` figure is the standout: BASELINE notes "weakest, header overhead dominates" - and V37 directly attacks that header overhead. The 33.5% baseline is on output where the header *is* most of the bytes; trimming `extensions:` -> `ext`, `13 files, 2 dirs across 1 dirs` -> `13f 2d/1`, and dropping `... +N more directories` to `+N dirs` collapses the header.

### Latency

Zero runtime cost. The phrasing change is a static format-string swap. Cold-start unaffected. Warm parse unaffected. Cache-key unaffected (argv-keyed, not output-keyed).

### Affects

- All 11 command-side compressors get touched at 1-3 sites each.
- File-side scorers / packers untouched.
- Cache layers untouched (output bytes change, but the cache is keyed on argv + cwd; existing entries become "stale-but-correct" - emitting the old longer output is wasteful but not wrong, and any natural eviction or re-run produces the new shorter output).
- MCP `_meta.redcon` unaffected (the schema name, level, token counts are produced by the compressor unchanged; just the `text` field is shorter).

## Implementation cost

- **LOC:** ~50 net (one-line edits in 26 places). The script itself is ~350 LOC and lives in tests/research, not production.
- **New deps:** none. tiktoken is already a project dep for canonical token counting in `redcon.core.tokens`.
- **Risks to determinism:** none - every winner is a static literal in the source. Same input still produces same output.
- **Risks to robustness:** none. The phrasing changes do not affect parser tolerance to malformed input.
- **Risks to must-preserve:** medium-low. The script's Pass-2 must-preserve filter catches the obvious violations (`branch:`, `\bcommit\b`, per-file uppercase markers). Risks remaining:
  - **Documentation drift.** Any external test or downstream tool that grep'd for `" passed"` or `"FAIL ... ("` in compressor output will silently break. Need to grep the test suite once before applying.
  - **Comprehensibility.** Some winners (e.g. `pytest 96p 0f /96 5.34s` instead of `pytest: 96 passed, 0 failed, (96 total) in 5.34s`) shift the format from human-readable prose toward agent-readable telegraphic. An LLM consumer will parse `96p 0f` correctly with high probability; a human triaging via `redcon run` on the CLI will find it harder. Mitigation: keep the wordier form at VERBOSE, apply the telegraphic form only at COMPACT and ULTRA. The script's per-template winners above target COMPACT.
  - **One-author ergonomics.** The format strings hardcode a particular phrasing decision per call site. Adding a new field later (e.g. `xfailed` count) requires re-running the V37 pass on the revised template. This is the cost of "baked-in optimum": brittleness to schema growth.
- **Risks to caching:** none structurally. Cosmetically, agents that tokenise the output as part of their *own* cache key (some MCP clients) will see a one-time cache miss on the day the format changes.

## Disqualifiers / why this might be wrong

1. **The savings *per output* are below the BASELINE.md 5-pp breakthrough bar on most compressors.** The headline numbers are 0.0-1.2 pp on git diff/pytest, 3 pp on grep/find, **12 pp on ls -R only**. That's a single-compressor breakthrough, not a multi-compressor one. The aggregate corpus saving (~280 tokens across 29 templates with toy inputs) sounds large but on real outputs the headers and rows are a small fraction; paths, code lines, and messages dominate, and V37 doesn't touch them.

2. **The wins overlap with V31 (multi-token-string substitution table).** V31's table-of-substitutions ("` failed`" -> "`F`", "`passed`" -> "`p`", "`directories`" -> "`d`") covers the same surface area through a different mechanism, and V31 is more general because it fires on output even when authors haven't curated the template. If V31 ships, V37's hand-tuning is a tiny incremental over the table-driven version. If V37 ships first, V31's table-substitutions on top of it find nothing.

3. **Author-time tuning rots.** The phrasings picked here are optimal *against the 4-5 sample inputs in the script*. A real distribution shift (e.g. monorepos with thousands of files, non-ASCII paths, very long commit subjects) might re-shuffle the per-template winners. Without an automated regression harness comparing periodically against fresh sample inputs, the bake-in becomes silently suboptimal as cl100k merges evolve (and as model providers move to o200k for new models, where the merges differ entirely).

4. **Some "wins" trade information for tokens.** `grep 250/38` saves 4 tokens vs `grep: 250 matches in 38 files` but assumes the agent reads positional `n/m` as `matches/files`. If the agent has the wrong mental model (`/` could be a path separator), this breaks comprehension. The must-preserve harness does not catch comprehensibility regressions; only humans do. The shipped phrasings often *sacrifice tokens for clarity* on purpose.

5. **Cross-tokenizer regression risk.** The candidate winners are optimal for cl100k. On o200k_base or llama-3 BPE, the merges differ - `directories` may be 1 token in one and 2 in another, and an agent that runs Redcon under a different tokenizer (V35 dynamic detection) gets *worse* output than the original. The fix - a tokenizer-conditional dispatch table - resurrects the runtime branching V37 deliberately avoided. So V37 is **structurally cl100k-only** unless paired with V35.

6. **Already partially shipped in disguise.** Recent commits ("indented continuation lines drop the 3-space prefix... saves ~1 token/line on cl100k", "`_normalise_whitespace` collapses 3+ newlines to 2", per BASELINE.md) are already micro-rephrasing edits. V37 generalises the discipline but the marginal additions over what shipped via inspection-driven rewrites are small. Three of 29 templates already optimal is evidence that the codebase's format-string discipline has been creeping toward this on its own.

7. **Test-suite churn.** Redcon's golden-file tests (`tests/test_diff.py`, `tests/test_grep.py`, etc.) likely encode the current phrasings literally. A V37 application means a 26-template test diff. Acceptable as one-shot, but doubles the LOC of the change.

## Verdict

- **Novelty:** low. This is a mechanical, exhaustive search over a tiny solution space at every existing template. The technique is folklore; what's new here is the per-template enumeration plus the must-preserve gate, neither of which is a research contribution.
- **Feasibility:** high. The script (~350 LOC) runs in <1 second; the production edits are trivial; the test-suite update is mechanical.
- **Estimated speed of prototype:** **1 day** including running the script, applying the 26 edits, updating the golden test fixtures, and re-running the quality harness.
- **Recommend prototype:** **conditional-on:** (a) accepting the cl100k lock-in (can't be ported to o200k without re-running the pass), (b) auditing each safe winner manually for human-readability regressions before shipping (`grep 250/38` is borderline; `pytest 96p 0f /96 5.34s` is over the line at COMPACT), (c) writing a CI regression check that re-runs the script and warns when a future template addition is sub-optimal.

The honest takeaway: V37 is a **diligence-tier improvement, not a breakthrough**. It moves `ls -R` compact-tier reduction by ~12 pp (the only above-bar win), nudges grep/find by ~3 pp, and gives small (<2 pp) gains everywhere else. Worth applying once, alongside a regression-ratchet harness, but the more interesting research surface in BASELINE.md (cross-call dictionary, snapshot deltas, log-pointer compounding) compounds across calls in a way V37's per-output gains can't.

### Artefact

Full evaluation script with all 29 templates, candidate phrasings, sample inputs, and the must-preserve harness lives at `/tmp/v37_phrasing/v37_greedy_phrasing.py`. Re-running it reproduces the tables above byte-for-byte (deterministic: tiktoken cl100k_base + static Python).
