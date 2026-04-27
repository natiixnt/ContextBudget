# V81: Hypothesis-style property-based fuzzing for must-preserve invariants

## Hypothesis
The current quality harness (`redcon/cmd/quality.py`) checks must-preserve patterns, determinism, and reduction floors against a small set of hand-crafted fixtures (`tests/test_cmd_quality.py`). Hand-crafted corpora over-fit to inputs the author thought of. A property-based fuzzer (Hypothesis) explores the input space with shrinkable, reproducible generators and finds inputs where invariants silently break. The claim: even with 11 well-tested compressors, fuzzing will find at least one class of must-preserve violation - typically caused by parser assumptions about input structure that hand-written tests do not stress (Unicode edge cases, byte-level shape collisions, boundary lengths). The campaign also yields a permanent regression net: any future parser refactor that re-introduces the same class is caught automatically.

## Theoretical basis
Property-based testing follows from the QuickCheck (Claessen and Hughes, 2000) and Hypothesis (MacIver, 2015) lineage. The core property is universal:

```
forall raw, level. P(compressor.compress(raw, level)) holds
```

with P being the conjunction of {determinism, must_preserve, no_crash, reduction_floor}. Hypothesis searches a generator's output space and shrinks counterexamples to minimal inputs. Coverage of strategy `S` over input domain `D` is bounded below by `|S(N)| / |D|` for `N` examples, but more importantly by the diversity of `S`'s reachable region. For a structured generator producing valid git diffs of bounded depth `d` with branching `b`, the reachable region is roughly `Theta(b^d)` distinct shapes; with `N=1000` we explore enough shape-classes to surface boundary-character bugs in O(50-200 examples) when one exists, by birthday-style coincidence on the bug-triggering byte set.

For this codebase the relevant probabilistic argument: every compressor uses `str.splitlines()`. Python's `splitlines()` recognises 9 separators (`\n \r \r\n \v \f \x1c \x1d \x1e \x85    `). A regex-based parser that anchors on `^pattern` instead matches only `\n` boundaries. Any character from `{\v \f \x1c \x1d \x1e \x85    }` embedded in a line that the parser expects to be intact will desync those two views. Hypothesis with a Unicode-broad alphabet hits one of those codepoints with high probability per generated path of length `L`: roughly `1 - (1 - 9/0x110000)^L` per path, multiplied by the path count per example. Empirically the broad-alphabet generator surfaces this in <50 examples.

## Concrete proposal for Redcon

New file `tests/test_cmd_quality_fuzz.py`. Opt-in via pytest mark `fuzz` so default test runs stay fast; CI can run it nightly.

```python
# tests/test_cmd_quality_fuzz.py
import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

from redcon.cmd.compressors.git_diff import GitDiffCompressor
from redcon.cmd.compressors.base import CompressorContext
from redcon.cmd.types import CompressionLevel
from redcon.cmd.quality import _force_level_hint

pytestmark = pytest.mark.fuzz  # `pytest -m fuzz` to enable

@st.composite
def git_diff_strategy(draw):
    n_files = draw(st.integers(0, 6))
    blocks = []
    for _ in range(n_files):
        path = draw(st.text(
            alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7e,
                                   blacklist_characters="/\\\\ "),
            min_size=1, max_size=8))
        blocks.append(_render_file_block(path, draw))
    return "".join(blocks)

@settings(max_examples=1000, deadline=None,
          suppress_health_check=[HealthCheck.too_slow],
          derandomize=True, database=None)
@given(git_diff_strategy())
def test_git_diff_invariants(text):
    raw = text.encode("utf-8")
    c = GitDiffCompressor()
    for level in (CompressionLevel.VERBOSE, CompressionLevel.COMPACT, CompressionLevel.ULTRA):
        ctx = CompressorContext(("git", "diff"), ".", 0, _force_level_hint(level))
        a = c.compress(raw, b"", ctx)
        b = c.compress(raw, b"", ctx)
        assert a.text == b.text, "non-determinism"
        if level != CompressionLevel.ULTRA:
            assert a.must_preserve_ok, f"must-preserve lost: {a.text!r}"
```

Add a parallel strategy + test for `pytest`, `grep`, and listing compressors. Two strategies per compressor: (a) "well-formed" matching realistic output shape, (b) "adversarial" using a broad Unicode alphabet, both feeding the same property assertions. Settings `derandomize=True` and `database=None` keep the campaign deterministic across CI runs (constraint #1 in BASELINE).

`pyproject.toml` gains:
```
[tool.pytest.ini_options]
markers = ["fuzz: property-based fuzzing, opt-in via -m fuzz"]
```

## Estimated impact
- Token reduction: 0 pp directly. Fuzzing is a quality net, not a compressor improvement.
- Latency: 0 on default `pytest`; +30-60s on `pytest -m fuzz` for the 4 compressors at 1000 examples each.
- Affects: `tests/` only. Production source untouched. The harness in `redcon/cmd/quality.py` is reused as-is.

What the campaign actually found in this 1000-example run:

| Campaign | Inputs | Crashes | Non-determinism | Must-preserve fail | Reduction-floor fail |
|---|---|---|---|---|---|
| git_diff (well-formed) | 1000 | 0 | 0 | 0 | 0 |
| pytest (well-formed) | 1000 | 0 | 0 | 0 | 0 |
| git_diff (adversarial Unicode) | 1000 | 0 | 0 | 11 | 0 |

The 11 must-preserve failures collapse to one root cause. Minimal repro:

```
diff --git a/file<U+001D>x b/file<U+001D>x
index 1234567..89abcde 100644
--- a/file<U+001D>x
+++ b/file<U+001D>x
@@ -1,3 +1,3 @@
 ctx
-old
+new
```
Replicated 6 times to push past `MIN_RAW_TOKENS_FOR_REDUCTION_CHECK=80`. At COMPACT the compressor emits `diff: 0 files, +0 -0` while the raw clearly contained `diff --git`. The raw `\bdiff --git\b` must-preserve regex is satisfied by the input but not by the output. **Real, deterministic, reproducible.**

Root cause: `_split_into_file_blocks` calls `text.splitlines()` (line 125 of `redcon/cmd/compressors/git_diff.py`). Python's `str.splitlines()` honours nine line terminators including `\v \f \x1c \x1d \x1e \x85    `. The `_DIFF_HEADER` regex anchors on the resulting truncated line, fails to match, the entire file block is dropped. Equivalent `str.splitlines()` calls exist in 10 other compressors (`listing_compressor`, `pytest_compressor`, `cargo_test_compressor`, `npm_test_compressor`, `git_log`, `docker_compressor`, `lint_compressor`, `kubectl_compressor`, `http_log_compressor`, ...). The bug class is system-wide; the fuzzer found it in the one compressor we asked about.

Real-world relevance: low but non-zero. Git itself quotes paths with C-style escapes when `core.quotepath=true` (default), so `\x1d` would appear as `\035` in stdout - safe. With `core.quotepath=false` (used by some teams to keep UTF-8 names readable) raw bytes pass through. ` ` and ` ` appear in real source files (JS minifiers historically emitted them); a diff over such a file is a plausible vector. Severity: a quality regression, not a security issue.

## Implementation cost
- Lines of code: ~150 for one fuzzer file covering 4 compressors. ~40 lines per additional compressor.
- New runtime deps: `hypothesis>=6.0` as a `tests-extra` (or `dev`) dep. **Does not violate "no required network / no embeddings"** because it is test-only. Default `pip install redcon` does not pull it.
- Risks to determinism: zero in production (test-only). Hypothesis itself is deterministic when `derandomize=True` and `database=None`. Risks to robustness/must-preserve guarantees: zero, this code does not run at user request time.

## Disqualifiers / why this might be wrong
1. **Already covered indirectly**: the existing `_check_robustness` in `quality.py` includes `b"\x00\x01\x02 binary garbage \xff\xfe"` and other adversarial blobs. It only checks "no crash", not must-preserve, but a maintainer might argue that adversarial-input must-preserve is over-specified and the right fix is to strip non-`\n` separators from raw input before parsing rather than expand the test surface.
2. **Real-world frequency is rare**: the bug needs `\v \f \x1c \x1d \x1e \x85    ` inside a tracked path or content line, which is unusual. A maintainer could legitimately mark this WONTFIX and the fuzzer's value drops to "regression net for things not yet broken."
3. **Hypothesis costs flake risk if mis-configured**: without `derandomize=True` and `database=None`, CI sees different counterexamples on different machines. Easy to mis-configure - adds operational burden.
4. **Generator quality is the limiting factor**: the well-formed strategies above caught nothing. The adversarial strategy caught the splitlines bug. Future researchers writing fuzzers must understand both ends, otherwise they get "1000 inputs, 0 violations" with false confidence (which is exactly what the well-formed-only campaign produced for both git_diff and pytest).
5. **Differential testing (V82) is arguably stronger**: comparing raw-text contains-X against compressed-text contains-X across a curated golden corpus catches the same class with no random variation.

## Verdict
- Novelty: low (property-based testing is 25 years old; specific to Redcon's must-preserve invariants is mildly novel).
- Feasibility: high. ~150 LOC, deterministic, runs in <60s.
- Estimated speed of prototype: hours.
- Recommend prototype: **yes**, with priority "merge after fixing the splitlines bug it surfaced." The fuzzer paid for itself in this one campaign by finding a real defect; the regression-net value going forward is a bonus. Suggested follow-on patch: replace `text.splitlines()` with `text.split("\n")` across compressors, or normalise non-`\n` separators upstream in `pipeline.compress_command`. That fix is small (one helper, applied at ~12 call sites) but **out of scope for this researcher** (no production source changes per task instructions).
