# V60: Online dedup via rolling hash shingles

## Hypothesis
Across the line stream emitted by a single compressor invocation, multi-line spans recur often enough that an online rolling hash over N-line shingles can detect them and replace later occurrences with a back-reference (e.g. `[see span_3]`). The targets the index proposes are realistic on raw input (similar stack frames in pytest FAILURES, similar diff-hunk bodies for repeated rename-only patches, similar "Step N/M" preambles in a docker build log) and intra-output dedup is theoretically attractive because it costs only a single pass over the buffered stream and adds no model-side state. Concrete claim: with 6-line shingles using a Rabin-Karp 64-bit rolling hash, for a 1 MiB raw docker build the compressor can reference around 5-15% of duplicated lines, saving 3-8 absolute percentage points off the COMPACT-tier reduction on top of the per-compressor logic. *On the actual COMPACT outputs that Redcon already emits, the claim collapses to near zero, for reasons quantified below.*

## Theoretical basis
Let the line stream be `L = l_1 ... l_M` and define the shingle at position i as `s_i = (l_i, ..., l_{i+N-1})`. Build a rolling polynomial hash `h_i` over the byte concatenation of `s_i` with separator. Maintain a dict `D : h -> i`. On scan, if `h_j` collides with an entry `D[h_j] = k < j` *and* the spans `s_k, s_j` byte-equal each other (collision check), emit `[span_k]` for span at j and skip ahead by N (or 1, depending on policy).

Saving per matched span (without overlap): `saved(N) = N * mean_line_tokens - C_ref`, where `C_ref` is the token cost of the reference marker. With `mean_line_tokens ~= 8` (cl100k empirical, see BASELINE.md whitespace-collapsing notes) and `C_ref ~= 5` for `[span_3]`, a 6-line shingle saves `6 * 8 - 5 = 43` tokens per match.

Total saving: `S = sum_over_matched_spans saved(N) = R * 43` where `R` is the number of matched spans. The crucial empirical question is: what is `R` on real outputs? For independent uniform line distribution with vocabulary size `V` and 6-line shingles over `M` lines, the expected exact-match collision count is

    E[R] ~= C(M, 2) * V^{-N}

For `M=200, N=6, V=1000` (effective unique-line vocabulary in compressed pytest output), `E[R] ~= 20000 * 10^{-18}` -- vanishing. For raw output (think docker logs), `V` is smaller but each line still carries timestamps / hashes / line numbers, pushing `V` per slot back up. The dependency on `V^{-N}` is what makes strict shingles so brittle: any per-line entropy injection (a SHA, a line number, a duration) sends collision probability to zero.

I ran a concrete probe on three synthetic but representative streams (paste in scratch, not committed):

  - **COMPACT git diff with 6 modified files** (19 lines): 6-line shingle repeats `0/14`, 3-line `0/17`. The compressor already emits one canonical line per file plus one hunk header; structure varies file-to-file (`+18 -10`, `@@ -33,5 +33,7 @@`).
  - **VERBOSE pytest with 5 failures, 3 of them in `tests/test_a.py`** (31 lines): exact 6-line shingle repeats `0/26`. With numeric normalization `re.sub(r'\d+','N',line)` the same stream yields 6 repeats at N=3, 4 at N=4, 2 at N=5, *0 at N=6*. The proposed 6-line size is the wall.
  - **Docker build log with 8 similar `Step k/12 : ... ---> hash` blocks** (27 lines): exact 6-line repeats `0/22`, even with hash + digit normalization, `0/22`. Each step's middle "Running in container-id" line breaks alignment.

So `R = 0` on the three obvious targets at the prescribed N=6. The vector's stated risk ("false matches at small shingle size, use 6") is correct in the false-positive direction but fatal in the recall direction: at N=6 the technique deduplicates almost nothing on the actual data.

## Concrete proposal for Redcon

Hook would live in `redcon/cmd/pipeline.py::compress_command` between `compressor.compress(...)` and `_normalise_whitespace(...)`. Standalone module `redcon/cmd/_shingle_dedup.py`:

```python
def shingle_dedup(text: str, *, n: int = 6, marker: str = "span") -> str:
    lines = text.splitlines()
    if len(lines) < n:
        return text
    table: dict[int, int] = {}      # hash -> first-seen line index
    spans: list[tuple[int, int]] = []   # (start, end_exclusive) of each replacement
    pos = 0
    while pos <= len(lines) - n:
        block = lines[pos : pos + n]
        h = _stable_hash(block)
        prior = table.get(h)
        if prior is not None and lines[prior : prior + n] == block:
            spans.append((pos, pos + n, prior))   # collision-checked
            pos += n
            continue
        table[h] = pos
        pos += 1
    if not spans:
        return text
    span_ids = {start: f"[{marker}_{i+1}]" for i, (start, _e, _p) in enumerate(spans)}
    out: list[str] = []
    skip_until = -1
    for i, line in enumerate(lines):
        if i < skip_until:
            continue
        if i in span_ids:
            ref = span_ids[i]
            _start, end, prior = next(s for s in spans if s[0] == i)
            out.append(f"{ref} (=lines {prior+1}-{prior+n})")
            skip_until = end
            continue
        out.append(line)
    return "\n".join(out)
```

`_stable_hash` is FNV-1a 64-bit over the joined block (or Rabin-Karp for true rolling, but with `n=6` over <100k-line streams the cost difference is negligible). Determinism is preserved: same input -> same shingle table -> same replacement order. Cache key (per BASELINE.md constraint #6) is unchanged because input bytes drive both. Robustness on truncated input, binary garbage, 5000 newlines: replacement only fires on byte-identical 6-tuples; in pathological repeats (5000 newlines) it would dedup the empty-line shingle once, saving 6 newlines. Harmless.

Wiring sketch in `pipeline.py`:

```python
out = compressor.compress(stdout, stderr, ctx)
if level in (CompressionLevel.VERBOSE, CompressionLevel.COMPACT):
    new_text = shingle_dedup(out.text)   # COMPACT/VERBOSE only; ULTRA already 1-line
    if new_text != out.text:
        out = replace(out, text=new_text, compressed_tokens=estimate_tokens(new_text))
```

The must-preserve harness in `redcon/cmd/quality.py` would need a tweak: when a must-preserve regex would have matched only inside a replaced span, the dedup should be backed out. Simplest defensive form: run `verify_must_preserve` after dedup; on fail, drop the dedup and use the raw compressor text.

## Estimated impact
- Token reduction on COMPACT tier:
  - git diff, pytest, grep, find, ls, lint, docker, kubectl: empirically `R ~= 0` on the post-compressor output (numbers above). Expected delta: **0.0 - 0.5 absolute points** averaged across compressors. Below the ~5pp BASELINE.md breakthrough threshold by an order of magnitude.
  - Where it could plausibly matter: log-pointer tier `tail -30` of a docker build with literal block repeats (e.g. retry-loop emissions, `failed to fetch X. retrying. failed to fetch X. retrying.`). Maybe 1-3 pp on that tail, conditional on the retry block being byte-identical -- usually it isn't.
  - VERBOSE on pytest with very similar failures: *if* numeric normalization is added (which is no longer "rolling-hash shingles" -- it's a different vector), 1-3 pp. With the strict N=6 rule from the task, ~0 pp.
- Latency: cold +1 import (`hashlib` is already imported). Warm: O(M) bytes hashed; for a 100k-line stream around 5 ms on cl100k-sized inputs. Within budget.
- Affects: `redcon/cmd/pipeline.py` (one call site), new `redcon/cmd/_shingle_dedup.py`, `redcon/cmd/quality.py` (defensive must-preserve guard). No cache-key change. No change to compressors themselves.

## Implementation cost
- Lines of code: ~80 net (50 in `_shingle_dedup.py`, 10 wiring, 20 harness guard).
- New runtime deps: none. Stdlib `hashlib` (or hand-rolled FNV-1a, ~10 lines).
- Risks to determinism: low (deterministic dict insertion order, deterministic hash). The collision-check (full byte equality after hash hit) eliminates the only nondeterminism source.
- Risks to robustness: low. Worst-case behaviour is "no dedup", not "wrong output".
- Risks to must-preserve: medium *unless* the post-dedup verify-and-rollback is implemented. Without rollback, a replaced span could swallow a regex-required substring. With rollback, neutral.

## Disqualifiers / why this might be wrong

1. **Empirically zero hits at N=6 on real compressor output** (probes above). The Theme F vector spec says "use 6-line shingles" to guard against false matches; that very choice is what makes recall vanish on outputs already structured to one canonical line per fact. The math `E[R] = C(M,2)/V^N` is brutal: every per-line entropy source (line number, hash, timestamp) multiplies V and crushes `R` toward zero. To get nonzero `R` you have to add normalization (digits -> N, hashes -> X), at which point you've left V60's actual specification ("rolling hash of literal shingles") and entered V64 (stack-trace template extraction) or V55 (online clustering of failure messages). Both are already in the index as separate vectors -- they own the recall regime where V60 has none.

2. **The compressors already do span-level dedup at parse time**, by structure rather than by hash. `git_diff._format_compact` emits one `M path: +I -D` line plus `@@ -a,b +c,d @@` per file -- an inherently dedup'd representation. `pytest._format_compact` emits one FAIL line plus first meaningful message line per failure. Adding a second-pass shingle dedup on output that has already been semantically deduplicated is a no-op on most inputs and a token-overhead loss on the rest (the `[span_k] (=lines a-b)` reference is itself ~5 tokens; if `R = 0`, you've added `O(N)` runtime for nothing). The "intra-call" use of dedup is precisely the niche where V60's ROI is worst, because COMPACT-tier output is approximately the maximum-entropy projection the compressor can produce.

3. **Where shingle-dedup actually wins is intra-RAW (before the per-compressor parser runs)**, e.g. on the `tail -30` of a log-pointer spill, on `find` output before the path-tree compression, or on a 50 MB raw `pytest -v` stream before the parser sees it. But that role is already filled by (a) the log-pointer tier itself (BASELINE.md: spill > 1 MiB), (b) `find` path-tree compression that explicitly merges identical sibling fragments, and (c) `_normalise_whitespace`. Any dedup gain in those slots has been preempted. So even when V60 *could* fire, the surrounding architecture has already absorbed the win.

4. **Cross-call dedup is what would actually move the metric**, and the index correctly assigns that to V41 (path aliases), V42 (hash-keyed shared dict), V43 (RAG-style hot store), V47 (snapshot delta), V48 (cross-tool dedup). V60's stated boundary ("intra-output, current call only") opts out of the regime where the gain lives.

## Verdict
- Novelty: low. Rabin-Karp shingle dedup is textbook (Manber 1994, Broder 1997's shingling for similarity detection, Henzinger 2006). Applied to compressor output, it competes against AST-aware structural dedup that already runs upstream.
- Feasibility: high. ~80 LoC, no deps, deterministic, robust by rollback.
- Estimated speed of prototype: 4-6 hours for the implementation plus harness guard plus a fixture-driven token-delta benchmark.
- Recommend prototype: **no**. Numbers don't support it. The recall floor at N=6 is essentially zero on the targets BASELINE.md cares about. If the goal is "find repeated multi-line stack-frame spans," V64 (stack-trace dedup with template extraction) and V55 (online failure-message clustering) own that lane, and they don't pay the strict-byte-equality tax. Recommend redirecting this researcher's effort to **V64 with normalised templates** as a strict superset that subsumes V60's design without its empirical dead end. If V60 is wanted purely as instrumentation -- a "would-have-deduped" counter alongside the existing reduction metric -- ship the 50-line `_shingle_dedup.py` module behind a `record_history=True`-style flag and read off the numbers; do not put it on the compress hot path.
