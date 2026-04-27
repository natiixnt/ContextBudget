# V40: Path canonicalisation - choose shortest relative path representation per tokenizer

## Hypothesis

Long file paths dominate compressor output for cohesive multi-file diffs / grep / find / lint reports, and cl100k tokenises every path segment independently with no merge for project-internal namespaces (`redcon/cmd/compressors/`). A path-formatter that (a) computes the path-set's longest common prefix, (b) declares it once in a "PACK" line, and (c) emits each row with the suffix only - or, if the schema banner already implies the pack, drops the header line entirely - reduces path-row tokens by 15-30% on cl100k and 25-35% on o200k for typical "redcon-internal" diffs. The dotted module form (`redcon.cmd.compressors.foo`) buys a smaller but consistent ~10% even without packs because `.` is a single token while `/` plus the next-segment-start often re-shatters merges.

This is purely a tokenizer-aware **format** change. No information is dropped. It is a strict win whenever K paths share any prefix and is monotone-safe to fold into existing compact-tier formatters.

## Theoretical basis

Let a path-row template be `M <P>: +X -Y`. Under cl100k each path `p_i` costs `t_i = |encode(p_i)|`. For K paths in one diff and a candidate prefix `q` shared by `m <= K` of them, replacing `q` in each of those `m` rows with an alias of cost `a` and declaring `q` once in a header of cost `h(q)` yields:

```
Delta(q) = m * (t_q - a) - h(q)
```

where `t_q = |encode(q)|`. The header-free schema-pack variant (where the schema banner already implies `q` so `h(q) = 0`) gives `Delta = m * (t_q - a)` and is positive whenever `m >= 1` and `t_q > a`.

Measured on cl100k_base for the 12-path diff of commit `d52c3879`:

| form | path-row only | full 12-row block | tok / row | bytes / tok |
|---|---|---|---|---|
| ABS  (rel-to-/)         | 281 path-tok | 376 | 23.4 | 3.73 |
| REL  (rel-to-repo, current) | 113 | 196 | 9.42 | 3.92 |
| PKG  (rel-to-`redcon/cmd/`) | 71 | 155 | 5.92 | 4.50 |
| DOT  (`redcon.cmd.compressors.x`) | 92 | 175 | 7.67 | 4.40 |
| DPKG (dotted + rel-to-pkg) | **59** | **143** | 4.92 | 4.71 |
| PACK header + suffix (PACK: `redcon/cmd/`) | -- | 161 | -- | -- |
| schema-pack (no header line) | -- | **155** | -- | -- |
| greedy-alias `$1=redcon/cmd/compressors/` | -- | 170 | -- | -- |

Baseline = REL = **196 tok**. Best plausible drop-in (`schema-pack`, header-free) = 155 tok = **-20.9%**. With dotted-PKG: **-27.0%**. Absolute paths (`ABS`) cost +91.8% over REL - a real risk if any compressor accidentally emits absolute paths.

**Slash vs dotted, single segment:** `redcon/cmd/compressors/git_diff.py` = 9 tok; `redcon.cmd.compressors.git_diff` = 7 tok. The `.` connector costs less than `/` partly because cl100k merges `.cmd.` and `.compressors.` more cleanly than `/cmd/` and `/compressors/`. Inspect: `redcon/cmd/compressors/` = `[1171, 444, 84133, 26149, 1911, 1105, 14]` = 7 ids. `redcon.cmd.compressors.` = `[1171, 444, 26808, 82786, 1105, 13]` = 6 ids. The `cmd.` and `compressors.` postfix-merges are pre-trained in cl100k.

**Break-even:** for prefix `redcon/cmd/compressors/` (7 tok) with full PACK header (9 tok), break-even at K=1.29 - i.e. as soon as **2 paths share it**, the scheme pays off, by `7m - 9` tok. With header folded into the existing schema/banner line (incremental cost 0), any K>=2 with a non-empty common prefix is net positive.

**Cross-tokenizer (o200k_base):** ABS 388, REL 208, PKG 147, schema-pack ~ same shape, DPKG 136. The savings in absolute pp are similar; the relative savings are slightly larger because o200k has finer literal merges and less help on `/cmd/`.

## Concrete proposal for Redcon

Add a path-formatter helper to `redcon/cmd/compressors/base.py` (sketch only - **do not modify production source per instructions**). Compressors that emit a path-set (git_diff, grep, lint, find, listing, kubectl, pkg_install) call it once with `(paths, level, tokenizer_hint)` and receive `(header_lines, formatter_fn)`. The formatter encodes each path with the chosen scheme. The cache key is unchanged because formatting is post-parse.

```python
# redcon/cmd/compressors/base.py  (additive)

from collections import Counter

def choose_path_form(
    paths: tuple[str, ...],
    *,
    encode: Callable[[str], int],            # returns token count
    schema_implies_root: str | None = None,  # banner already implies this prefix
    enable_dotted: bool = False,             # only for python compressors
) -> tuple[tuple[str, ...], Callable[[str], str]]:
    """Pick the shortest-tokenising rendering for this path-set."""
    # Candidate prefixes: every directory boundary that occurs >=2 times.
    cand: Counter[str] = Counter()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            cand["/".join(parts[:i]) + "/"] += 1
    cand = {k: v for k, v in cand.items() if v >= 2}

    # Pick the prefix with max gain = m * (encode(q) - encode("$1/")) - encode("PACK: q")
    best_q, best_gain = None, 0
    for q, m in cand.items():
        gain = m * (encode(q) - encode("$1/")) - (
            0 if q == schema_implies_root else encode(f"PACK: {q}\n")
        )
        if gain > best_gain:
            best_q, best_gain = q, gain

    if not best_q:
        return ((), lambda p: p)
    header = () if best_q == schema_implies_root else (f"PACK: {best_q}",)
    def fmt(p: str) -> str:
        if p.startswith(best_q):
            return "$1/" + p[len(best_q):]
        return "^/" + p   # outside the pack
    return header, fmt
```

git_diff `_format_compact` becomes:

```python
paths = tuple(f.path for f in result.files)
header, fmt = choose_path_form(paths, encode=lambda s: len(_enc(s)),
                               schema_implies_root=None)
lines = [diff_summary_line, *header]
for f in result.files:
    lines.append(f"{marker} {fmt(f.path)}{rename}: +{f.insertions} -{f.deletions}")
```

Keep slash form by default. Enable dotted only for compressors whose paths are guaranteed-Python-modules (pytest tracebacks, lint with `pylint`/`mypy`). Never enable dotted in git_diff: paths there can include non-py files where `.` becomes ambiguous with extension dots.

## Estimated impact

- **Token reduction (path-rows only):**
  - git_diff compact, 12 cohesive paths under one pack: **-20.9% on path payload** (155 vs 196 tok, cl100k). Diff-banner + per-file `+/- counts` are unchanged, so the **net diff-output reduction depends on path:metadata ratio**. For file-heavy compact diffs (no hunk-header, no rename text) paths are ~70% of bytes, so expected end-to-end diff-compact reduction ~ **0.7 * 21% = 15 percentage points absolute saving on the path payload**, or **~3-5 pp on the total compact-tier reduction** (currently 97.0%). Diff already at 97% leaves little room; the bigger win is on grep (76.9%), lint, listing where path:metadata is even more skewed.
  - grep compact (heavy path repetition): expected **+5-8 pp** on top of current 76.9% because grep typically has K=20-200 paths under a small set of packs.
  - lint / pkg_install / kubectl: similar **+3-7 pp** depending on path-set cohesion.
- **Latency:** one extra `Counter` and a few `encode()` calls per compressor invocation. ~0.5-1.5 ms added on warm. No cold-start regression because `Counter` is stdlib.
- **Affects:** `git_diff.py`, `grep_compressor.py`, `find` (in listing_compressor.py), `lint_compressor.py`, `pkg_install_compressor.py`, `kubectl_compressor.py`. Doesn't touch cache, scorers, or pipeline.

## Implementation cost

- Lines of code: ~50 in `base.py` for `choose_path_form` + 5 lines per compressor to call it. Estimate: **~120 LOC total**.
- New runtime deps: none.
- Risks:
  - **must-preserve regexes:** git_diff currently asserts paths survive raw. The regex `^[A-Z] [^\s]+` still passes when path becomes `M $1/foo.py` (it's not a whitespace). Need to extend `must_preserve_patterns` to allow the `$1/...` rewrite, or run the verifier *before* path rewriting (cleaner, recommended).
  - **determinism:** `Counter.most_common` is stable on equal counts only by insertion order. We must tie-break deterministically by sorted prefix string, not by Counter iteration order.
  - **reverse mapping:** the agent reading `$1/foo.py` must understand the `PACK:` header. This is just a one-line convention; no agent state needed because every compressed output is self-contained. Robust.
  - **outside-pack rows:** `^/foo/bar.py` looks odd. Alternative: keep the bare path (no sigil) and require the agent to infer non-prefix-membership from absence of `$1/`. Saves ~1 tok per outside row, slight readability hit.

## Disqualifiers / why this might be wrong

1. **git_diff is already at 97.0% reduction.** The remaining 3% is dominated by the diff banner, rename markers, and the first-hunk header. Path-row savings on top might only buy ~1 pp net. The bigger upside is on grep / lint / listing - need to validate per-compressor.
2. **Cohesion assumption.** The methodology used a 12-path commit where 9/12 share `redcon/cmd/compressors/`. Real agent diffs span diverse roots more often than this; on a diff touching `tests/`, `docs/`, and `redcon/`, the longest common prefix collapses to "" and the scheme degrades to no-op. Need a corpus measurement, not a single-commit anecdote.
3. **The schema-pack 155-tok number cheats.** It assumed `redcon/cmd/` is implied by the schema banner with header_cost=0. In reality the banner is shared across all `git_diff` outputs and cannot embed per-call prefix info without expanding the banner. Realistic header-cost=4-6 tok, dropping the saving to ~17%.
4. **Already partially done.** BASELINE notes "normalise paths" as an existing cl100k-byte-pair-aware trick. Without code-diving deeper than this report covers, it's plausible some path normalisation is already in place; would need to confirm in `_normalise_whitespace` and `_format_compact` of each compressor. This research note inspected `git_diff.py` and confirmed: **no prefix factoring is in place there**. Other compressors not audited here.
5. **V99 (custom BPE) subsumes most of this gain.** A custom tokenizer would merge `redcon/cmd/compressors/` into one id, recovering up to 54 tok of the 196-baseline (27.6%) automatically without any format change. If V99 ships, V40 contributes only the *cross-tokenizer-portable* slice. V40's value is that it works **today** with cl100k/o200k unchanged.
6. **Output is plain-text for an LLM.** `$1/foo.py` is mildly less readable than the full path. Agents handle it fine in practice but a strict-readability tier (VERBOSE) should opt out.

## Verdict

- **Novelty:** medium. Common-prefix factoring is folklore; doing it tokenizer-aware-greedily with break-even math and applying to the existing compressor surface is incremental. Cross-cuts with V31 (multi-token substitution) and is a strict subset of V99 (custom BPE).
- **Feasibility:** high. Pure additive, no deps, no determinism risk if tie-breaks are sorted, regression bounded.
- **Estimated speed of prototype:** **half a day** to land in `base.py` + git_diff + grep, plus 0.5 day for fixtures and a corpus measurement across the 11 compressors.
- **Recommend prototype:** **conditional-on** (a) corpus measurement showing >=3 pp average lift across grep/lint/listing/find, AND (b) V99 not being prioritised the same quarter (V99 dominates this entirely if it ships).

## Numbers reference (cl100k_base, commit `d52c3879`, 12 paths)

```
form              path-tok   12-row tok   delta vs REL
ABS                   281         376        +91.8%
REL  (current)        113         196          0.0%   <-- baseline
PKG  (rel-pkg)         71         155        -20.9%
DOT  (dotted)          92         175        -10.7%
DPKG                   59         143        -27.0%
PACK header + suffix   --         161        -17.9%
schema-pack            --         155        -20.9%
greedy-alias $1=...    --         170        -13.3%
```

V99 upper bound on the same baseline: ~54 tok recoverable if `redcon/cmd/compressors/` becomes one merge -> **155 tok** (matches schema-pack, by coincidence: the header-free PACK rewrites the prefix down to ~1 token equivalent). V99 wins on **any** prefix in any tokenizer; V40 wins **only** on cohesive path-sets but ships today.
