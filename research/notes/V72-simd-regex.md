# V72: SIMD-accelerated regex via pyhyperscan or rust extension

## Hypothesis

Compressor parse paths are regex-bound: every line of grep / git diff /
git log / lint / docker output is run through 1-3 `re.compile`d patterns,
plus the `verify_must_preserve` step at the end re-compiles and re-scans
each `must_preserve_patterns` entry against both raw and formatted text.
Across the 11 compressors there are 81 `re.compile / re.match / re.search`
sites. CPython's `re` is a serial backtracking NFA in pure C, ~50-200 MB/s
on the typical patterns we use. Hyperscan (Intel, BSD) and Rust's `regex`
crate compile a *set* of patterns into a single multi-pattern DFA / Aho-
Corasick prefilter and scan with SSE4.2 / AVX2 at 1-5 GB/s, often 10x
faster than libc-class `re`, on real grep / diff payloads.

The claim: replacing `re` in the parse hot loops with a SIMD multi-pattern
matcher cuts **warm** parse latency by 4-10x on the bigger fixtures
(grep at 1.32 ms / git_diff at >=1 ms in BASELINE) without changing token
output. Expressed in the units BASELINE constraint #5 cares about, that
saves roughly 0.8-1.1 ms per warm call but **regresses cold-start by
~50 ms** (Hyperscan import) - which is exactly the trade BASELINE #5
forbids unless we lazy-load. The honest verdict is therefore: this is a
PERF vector, not a token-reduction vector, with non-trivial install-side
cost. It is only a breakthrough if (a) we lazy-import behind first
relevant compressor invocation (preserving cold-start), and (b) the
warm-side win is large enough to matter to a CLI that already runs in
single-digit milliseconds.

## Theoretical basis

A single regex on a string of length n with a pattern of size m runs in
O(n*m) worst-case for CPython (`sre_parse` -> `_sre.SRE_Pattern.match`
walks an NFA with backtracking). The constant factor is ~5 ns/char on
modern x86 for ASCII, ~20 ns/char for unicode-aware classes. Empirically:

```
   t_re(n)  ~=  5e-9 * n * k   (k patterns scanned serially per line)
```

Hyperscan compiles k patterns into a single non-backtracking matcher and
processes input at SIMD width w (16 bytes/cycle on SSE4.2, 32 on AVX2)
through a vectorised Aho-Corasick + Glushkov-NFA hybrid:

```
   t_hs(n)  ~=  ceil(n / w) * c_simd  +  k_match * c_callback
            ~=  0.3 - 0.8 ns/char  +  per-match callback overhead
```

For grep parse, the inner loop scans each line against `_INLINE` and
`_INDENTED`. With the prefix-gating already in place, k_eff is ~1.2 (digit
fast path resolves cleanly most of the time). The remaining cost is
dominated by `re.compile` overhead amortised once + per-line `match()`
call dispatch into `_sre`. Profiling lower-bound on a 100-line grep:

```
   re path:    100 lines * 80 chars * 5 ns  ~= 40 us regex
             + 100 lines * 600 ns dispatch  =  60 us  ->  100 us total
   hs path:    8000 chars / 16 B * 1 ns     ~= 0.5 us scan
             + 100 callbacks * 200 ns       =  20 us
             ->  total ~ 21 us
```

So a ~5x speedup on the *regex portion* of grep parse is plausible.
However, BASELINE's grep warm at 1.32 ms is the *whole* compress() path
including `text.splitlines()`, dataclass construction, `_format`, and
`estimate_tokens` on raw + compressed. Regex is at most ~10-15% of that.
Regex cost on a typical fixture might be 0.15 ms; SIMD shrinks that to
~0.03 ms. **Net warm speedup on the full pipeline: 7-10%, not 5x.**

The bigger win is in `verify_must_preserve`: it compiles each pattern in
the tuple at every call (no caching across calls because the pattern
tuple is a property), runs `regex.search(original)` then
`regex.search(text)`. For grep, patterns are dynamically built from path
list - the cache miss is per-invocation. A pattern *set* compiled in
one Hyperscan database lets us hit all paths in one O(n) pass. Worst-case
for the current code: 50 paths -> 50 compile + 50 search calls on
`original`. SIMD set-scan does it in 1 pass.

## Concrete proposal for Redcon

New file `redcon/cmd/_simd_regex.py` (lazy-loaded). Exposes a thin
adapter that mimics the subset of `re` we use:

```python
# redcon/cmd/_simd_regex.py
from __future__ import annotations
from typing import Iterable
try:
    import hyperscan as _hs   # optional dep
    _HAVE_HS = True
except ImportError:
    _hs = None
    _HAVE_HS = False

_DB_CACHE: dict[tuple[str, ...], object] = {}

def compile_set(patterns: tuple[str, ...]):
    """Compile a set of patterns into one matcher. Caches by tuple identity."""
    if patterns in _DB_CACHE:
        return _DB_CACHE[patterns]
    if _HAVE_HS:
        db = _hs.Database()
        db.compile(expressions=[p.encode() for p in patterns],
                   ids=list(range(len(patterns))),
                   flags=[_hs.HS_FLAG_MULTILINE] * len(patterns))
        scratch = _hs.Scratch(db)
        out = ("hs", db, scratch)
    else:
        import re
        out = ("re", tuple(re.compile(p, re.MULTILINE) for p in patterns))
    _DB_CACHE[patterns] = out
    return out

def any_unmatched(matcher, text: bytes, original: bytes) -> bool:
    """Return True if any pattern matched in original but not in text."""
    kind = matcher[0]
    if kind == "hs":
        _, db, scratch = matcher
        hit_orig: set[int] = set(); hit_text: set[int] = set()
        db.scan(original, match_event_handler=lambda i, *a: hit_orig.add(i),
                scratch=scratch)
        db.scan(text, match_event_handler=lambda i, *a: hit_text.add(i),
                scratch=scratch)
        return any(i in hit_orig and i not in hit_text for i in range(...))
    # fallback re path: identical to current verify_must_preserve
    ...
```

Wire it into `redcon/cmd/compressors/base.py::verify_must_preserve` only
when `len(patterns) >= 4` (otherwise the import + compile cost dominates
the savings). Behind env flag `REDCON_SIMD_REGEX=1` for the first
release; default off.

The grep parse loop is **not** rewritten - keep `re` there; the prefix
gating already shaves the regex cost to a level where SIMD-rewriting
the inner loop adds risk without commensurate reward.

## Estimated impact

- **Token reduction: 0 absolute pp.** This vector does not change output.
  Flag explicitly: PERF, not compression.
- **Warm-call latency:** -7% to -12% on grep / git_diff / lint when
  `must_preserve_patterns` set has >=4 entries. ~0 effect on small
  fixtures (overhead of cache lookup wash).
- **Cold-start latency:** **+50 ms regression** if Hyperscan loads
  eagerly. Lazy-import (only on first call to `verify_must_preserve` with
  a 4+ pattern set) reduces this to ~50 ms one-time tax on the compressor
  that triggers it. BASELINE #5 says "lazy-imports already shaved ~62%
  off cold-start; new techniques cannot regress this", so eager import is
  disqualified.
- **Affects:** `compressors/base.py` (`verify_must_preserve`),
  `compressors/grep_compressor.py` (path-set verification path),
  `compressors/git_log.py`, `compressors/git_status.py`, and all test_*
  compressors via `must_preserve_patterns_for_failures` (which can
  generate dozens of patterns when there are many failures - the largest
  beneficiary).

## Implementation cost

- **Lines of code:** ~120 in `_simd_regex.py`, ~10 in `base.py`. Plus
  ~80 lines of tests asserting fallback correctness (re path == hs path
  on the BASELINE quality fixtures).
- **New runtime deps:** `python-hyperscan` (BSD-3) wheels available for
  Linux x86_64 only on PyPI. macOS arm64 - the platform this repo
  develops on - has **no published wheel**, would require a brew
  install of `hyperscan` first plus building from source. Rust regex
  via PyO3 (`rure`) has wheels for more platforms but adds rustc as a
  build-time dep for sdist installs. Either choice violates the spirit
  of "no required network" because pip wheel availability is uneven and
  fallback compile paths fetch sources. Mitigation: keep `re` as the
  required path; SIMD as opt-in extra (`pip install redcon[simd]`),
  default-off env flag.
- **Risks to determinism:** Hyperscan reports matches in arbitrary
  order; we only use it for set-membership ("did pattern i match yes/no")
  so this is safe. **Risks to robustness:** Hyperscan rejects some PCRE
  features (lookbehinds, backrefs); patterns with these must auto-fall
  back to `re`. **Risks to must-preserve:** none, as long as fallback is
  reliable - covered by golden test asserting hs path and re path return
  identical bool.

## Disqualifiers / why this might be wrong

1. **Regex isn't actually the bottleneck.** Grep warm is 1.32 ms; profiling
   would likely show `text.splitlines()` plus `estimate_tokens` (which
   tokenises raw + compressed via cl100k) dominate. Saving 0.05 ms of
   regex work in a 1.32 ms budget is 4%, not the 5x the headline implies.
   The honest expected impact is in the 5-10% range on warm parse, well
   below BASELINE's "20% cold-start" breakthrough threshold.
2. **Cold-start regression is the wrong dimension.** Even with lazy-load,
   the *first* grep call in any process pays the 50 ms Hyperscan import.
   For a CLI where the median user does one or two `redcon run` calls,
   that first call gets *slower*, not faster. The break-even is roughly
   5-7 calls per process; in MCP server mode that's fine, in CLI mode
   it's a regression for the typical user.
3. **Already-implemented in disguise.** BASELINE explicitly notes
   "Pre-compiled regex globals audit - find misses" as V78 (open). Many
   compressors already hoist `re.compile` to module scope (see
   `_INLINE`, `_INDENTED`, `_DIFF_HEADER`, `_HUNK_HEADER`). The remaining
   `re.compile` calls inside `verify_must_preserve` are the only obvious
   miss; a pure-Python fix (cache compiled patterns by tuple identity)
   captures most of the SIMD gain at zero install cost. V78 dominates
   V72 on the cost / benefit ratio.
4. **Wheel matrix.** No macOS arm64 wheel for `python-hyperscan` means
   the developer of this repo cannot install it without homebrew gymnastics.
   `rure` (Rust regex) has wider coverage but no `match_set` analog -
   one would need `regex-automata`'s set-DFA via a custom Rust crate.
   The minute we ship a custom crate, "no required network" collapses
   unless we vendor pre-built wheels for every platform.
5. **The patterns are short and ASCII.** Most `must_preserve_patterns`
   are <40 bytes. SIMD wins are concentrated on long alternation-heavy
   patterns over MB-scale inputs. Our inputs are typically <100 KB
   (log-pointer tier kicks in above 1 MiB) and patterns are tiny.
   The asymptotic argument (1 GB/s SIMD scan) doesn't apply at our
   working scale.

## Verdict

- **Novelty: low.** SIMD regex is a known optimisation; applying it to
  must-preserve verification is the only mildly novel angle. V78
  (pre-compiled globals audit) likely captures 70-80% of the available
  win at <5% of the cost.
- **Feasibility: medium.** Lazy-import adapter is straightforward; wheel
  matrix and BSD-only build path raise install friction.
- **Estimated speed of prototype:** 1-2 days for adapter + golden tests
  on Linux. macOS arm64 needs ~half a day of brew + build wrangling.
- **Recommend prototype: no.** Recommend instead: do V78 first
  (pre-compile and cache `must_preserve_patterns` at module scope where
  static, memoise dynamic ones by tuple key). That captures the easy
  half of this win in pure Python, with zero deps, zero cold-start tax,
  and reaches the 5-7% warm-side improvement floor without violating
  any BASELINE constraint. Revisit V72 only if profiling on real MCP
  server workloads shows verification-time regex >25% of warm parse.
  This vector is **conditional-on-V78-being-insufficient**.
