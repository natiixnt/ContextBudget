# V04: Kolmogorov-complexity proxy via 5-codec ensemble min-length, used as a per-input compressor floor

## Hypothesis

Kolmogorov complexity K(x) is uncomputable, but for the kinds of strings Redcon
produces (ASCII text with strong local Markov structure: diff hunks, file paths,
test names, log lines), the minimum compressed length over a small ensemble of
practical universal coders is a tight, principled empirical lower bound on
"irreducible information". If we run gzip / lzma / bzip2 / zstd / brotli at
maximum effort against the raw command output and against Redcon's compact
output, the gap between (a) Redcon's compact-tier token count and (b) the codec
floor translated to tokens via a calibrated bytes-per-token ratio gives a per
fixture, per-call answer to "is this compressor at the floor or does it have
headroom?". Operationalised as a `compression_headroom` field in `run.json`,
this turns `redcon cmd-bench` into a closed-loop optimisation target rather than
a leaderboard of historical bests.

## Theoretical basis

Per Li and Vitanyi, "An Introduction to Kolmogorov Complexity and Its
Applications" (3rd ed., Theorem 7.3.1 and surrounding), for any universal
prefix-free compressor C and any string x,
  K(x) <= |C(x)| + O(log |C|)
and conversely no compressor can beat K(x) by more than an additive constant.
Concretely, for an ensemble of compressors C_1..C_k all universal in the
Solomonoff sense, the per-string lower bound
  K(x) >~ min_i |C_i(x)|
holds up to a constant. Using k > 1 is meaningful because each practical coder
is universal only relative to its model class (LZ77 dictionaries for
gzip/zstd/brotli, BWT block-sort for bzip2, range-coded LZMA dictionaries for
xz). The min over the ensemble is the operational K-proxy used by, e.g.,
Cilibrasi and Vitanyi's "Clustering by Compression" (IEEE T-IT 51, 2005),
section III.A.

Back-of-envelope for the "headroom" signal. Let
  R = redcon-compact-bytes,
  F = min_i |C_i(raw)|  (codec floor on raw bytes),
  rho = compact-bytes / compact-tokens  (the calibration ratio measured on
        redcon output, which is what we actually charge for).
Then a token-space lower bound on any lossless representation of the same
information is F / rho, and the slack the compressor leaves over that floor is
  H = R / rho - F / rho = (R - F) / rho   tokens.
Because Redcon is *lossy* (drops hunk bodies, dedups paths, summarises pass
counts), H can be negative, which is the desirable case: it means we paid less
than even a lossless universal coder could in principle. When H is large and
positive, structural redundancy still exists in the compact text and a smarter
serialisation could close it.

A second derivation is the recompress test. If `min_i |C_i(R)|` is much smaller
than R, the compact text has significant local Markov redundancy in its own
serialisation choices (verbose headers, repeated literals, indent prefixes).
This is independent of the lossy-vs-lossless question above and is purely about
how the compressor chose to spend its byte budget after deciding what to keep.

## Concrete proposal for Redcon

Add a tiny telemetry+benchmark layer. No production hot-path change. Five files
touched.

1. New `redcon/cmd/_codec_floor.py` (~80 LOC), stdlib only:
   ```python
   import bz2, gzip, lzma
   try: import zstandard
   except Exception: zstandard = None
   try: import brotli
   except Exception: brotli = None

   def codec_floor_bytes(buf: bytes) -> dict[str, int]:
       out = {
           "gzip":  len(gzip.compress(buf, 9)),
           "lzma":  len(lzma.compress(buf, preset=9 | lzma.PRESET_EXTREME)),
           "bzip2": len(bz2.compress(buf, 9)),
       }
       if zstandard:
           out["zstd"] = len(zstandard.ZstdCompressor(level=22).compress(buf))
       if brotli:
           out["brotli"] = len(brotli.compress(buf, quality=11))
       return out

   def floor_min_bytes(buf: bytes) -> int:
       return min(codec_floor_bytes(buf).values())
   ```

2. New `redcon/cmd/_calibration.py`: a one-shot bytes-per-cl100k-token
   calibration computed on the M8/M9 fixture corpus, baked in as a constant
   table per schema (`git_diff: 2.40, pytest: 3.32, grep: 2.59, ...`). Falls
   back to a global mean (`~2.65 B/tok` from the measurements below) when the
   schema has no entry. Recomputed offline by `python benchmarks/calibrate_btoks.py`.

3. `redcon/cmd/pipeline.py::compress_command`: when `record_history=True` or
   `RUNCONFIG.telemetry_headroom`, after compression compute
   `floor = floor_min_bytes(raw_stdout + raw_stderr)` and add to the report:
   ```python
   meta_hl = compressed.metadata.get("redcon", {})
   meta_hl["headroom"] = {
       "codec_floor_bytes": floor,
       "calibrated_floor_tokens": int(floor / btoks_for(schema)),
       "compact_tokens": compressed.token_count,
       "headroom_tokens": compressed.token_count - int(floor / btoks_for(schema)),
       "winning_codec": min(codec_floor_bytes(raw), key=lambda k: codec_floor_bytes(raw)[k]),
       "compact_recompress_ratio": floor_min_bytes(compressed.text.encode("utf-8")) / max(1, len(compressed.text)),
   }
   ```
   Cost: one extra 5-codec compress on raw bytes. Capped at 64 KiB of input
   (skip when `raw_bytes > 65536`) so we never blow latency on huge logs.

4. `benchmarks/run_cmd_benchmarks.py`: emit a `headroom` column per fixture in
   the markdown table and per-schema summary. Plumb through the Benchmark dataclass.

5. `redcon/cmd/quality.py::run_quality_check`: when headroom_pct > 70% and
   raw_tokens > 1000, raise a *non-failing* warning ("compressor X has 70%+
   headroom on fixture Y; consider tightening"). This is a research signal,
   not a gate.

API stays a strict superset of today's. Default code path is unchanged. Only
turning on `telemetry_headroom` in config or running the new bench script
exercises the codec floor.

## Estimated impact

- **Token reduction**: zero direct. Indirect: this is a *measurement*
  facility. It identifies which compressors have headroom worth investing in.
  Empirically (see numbers below) it flags `git_diff_huge` (+98% headroom),
  `grep_massive` (+93%), `find_massive` (+91%), `ls_huge` (+92%), `ruff_typical`
  (+90%), `mypy_large` (+81%), `pytest_massive` (+83%) as the ones where 5-10pp
  COMPACT improvements should be reachable; conversely `pytest_small`,
  `git_status`, `cargo_test`, `go_test`, `ls`, `pip_install`, `tree`,
  `docker_build` are at or below the codec floor (negative or near-zero
  headroom), confirming further tier-2 work on those is wasted.

- **Latency**: brotli at quality=11 + lzma extreme are slow. On the largest
  fixture in the corpus (`git_diff_huge`, 32 KiB) the full 5-codec pass
  takes about 30-90 ms cold. With the 64-KiB skip cap and an off-by-default
  flag, default warm `redcon_run` is unaffected. Bench-only path absorbs
  the cost.

- **Affects**: `redcon/cmd/pipeline.py` (additive metadata), `benchmarks/`,
  `redcon/cmd/quality.py` (advisory only). No scorer changes. No cache-key
  changes (signal is in metadata, not in argv).

## Implementation cost

- Lines of code: ~250 (codec_floor 80, calibration 60, pipeline glue 30,
  bench output 50, quality advisory 30).
- New runtime deps: `zstandard` and `brotli` are *optional* (graceful skip,
  exactly as the script in `/tmp/v04_kolmogorov.py` already does). gzip /
  lzma / bz2 are stdlib. No network, no embeddings, no determinism break.
- Risks:
  - Brotli/lzma at max effort are nondeterministic across versions in
    *byte content*, but **size** is deterministic for a fixed library
    version. Telemetry value (the integer length) does not violate the
    same-input-same-output contract; pin minor versions in `pyproject.toml`.
  - Adding any codec to a hot path could regress cold-start. Mitigation:
    lazy-import inside the telemetry function, gated by config flag.
  - Calibration table drift: if cl100k's bytes-per-token shifts on a future
    tokenizer update, the *reported* token-floor estimate becomes inaccurate
    while the byte floor stays correct. Mitigation: ship a `calibrate` CLI
    and re-run it as part of release prep.
  - Quality-harness must NOT fail on headroom warnings (information signal,
    not a regression). Already accounted for above.

## Empirical numbers from fixtures (21 fixtures, M8/M9 corpus)

Run with `python /tmp/v04_kolmogorov.py` against
`tests.test_cmd_quality.CASES`. zstd, brotli, tiktoken all available;
codec floor uses gzip-9, lzma-9-extreme, bzip2-9, zstd-22, brotli-q11.

Per-fixture (raw bytes / raw cl100k tok / B-per-tok / floor min B / floor B
which codec wins / compact tok / headroom tok / headroom % of compact):

| fixture | raw B | raw tok | B/tok | floor B | win | compact tok | headroom tok | headroom % |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| git_diff_small | 243 | 114 | 2.13 | 156 | brotli | 89 | +6 | +7.1 |
| **git_diff_huge** | 32312 | 13260 | 2.44 | 423 | brotli | 9277 | **+9099** | **+98.1** |
| git_status | 64 | 23 | 2.78 | 67 | zstd | 27 | +4 | +15.2 |
| git_log | 256 | 91 | 2.81 | 154 | brotli | 64 | +6 | +8.9 |
| pytest_small | 830 | 149 | 5.57 | 273 | brotli | 82 | +2 | +2.5 |
| **pytest_massive** | 10220 | 2442 | 4.19 | 733 | brotli | 1340 | **+1114** | **+83.2** |
| cargo_test | 336 | 97 | 3.46 | 167 | brotli | 39 | -13 | -32.5 |
| go_test | 167 | 62 | 2.69 | 118 | brotli | 18 | -23 | -126.9 |
| npm_test_jest | 274 | 78 | 3.51 | 154 | brotli | 39 | -4 | -10.0 |
| grep_small | 75 | 29 | 2.59 | 68 | brotli | 38 | +11 | +29.2 |
| **grep_massive** | 28059 | 10200 | 2.75 | 1185 | zstd | 6460 | **+6003** | **+92.9** |
| ls | 162 | 79 | 2.05 | 100 | brotli/zstd | 30 | -18 | -61.3 |
| **ls_huge** | 6169 | 3300 | 1.87 | 485 | brotli | 3339 | **+3077** | **+92.2** |
| tree | 57 | 19 | 3.00 | 54 | brotli | 22 | -1 | -5.9 |
| find | 46 | 17 | 2.71 | 35 | brotli | 28 | +13 | +46.2 |
| **find_massive** | 13589 | 5999 | 2.27 | 672 | lzma | 3409 | **+3110** | **+91.2** |
| **mypy_large** | 2928 | 1008 | 2.91 | 488 | brotli | 924 | **+746** | **+80.7** |
| **ruff_typical** | 4765 | 1576 | 3.02 | 465 | brotli | 1693 | **+1530** | **+90.4** |
| docker_build_typical | 778 | 248 | 3.14 | 388 | brotli | 95 | -24 | -25.2 |
| pip_install_typical | 682 | 256 | 2.66 | 288 | brotli | 56 | -95 | -169.2 |
| kubectl_pods_typical | 704 | 245 | 2.87 | 212 | brotli | 132 | +56 | +42.2 |

Aggregate calibration (the second deliverable from the prompt):

- avg raw B/tok          = 2.925
- avg compact B/tok      = 2.652
- avg compact recompress = 58.6 % (min-codec(compact) / compact bytes)
- avg headroom over floor = +16.6 %
- codec-floor winners (out of 21): brotli 17, zstd 3, lzma 1.
  Brotli at q=11 wins almost universally on Redcon-shaped inputs (small
  English-y vocabulary + repeated path stems). gzip and bzip2 never win.
  An *ensemble of just three* (brotli, zstd, lzma) reproduces the exact
  same min on every fixture - production telemetry can drop gzip/bzip2.

Reading these results:

1. The big-fixture compressors (`git_diff_huge`, `grep_massive`, `find_massive`,
   `ls_huge`, `ruff_typical`, `mypy_large`, `pytest_massive`) all show 80-98%
   headroom. This *quantifies* the open frontier: the lossy compressors
   are not exploiting all redundancy that even an unlabelled universal
   coder finds. Per-schema cross-call dictionary (V41-V50) and template
   extraction (V13) are the obvious next moves.

2. The recompress ratio of compact output averages 58.6 % - that means
   running brotli/zstd over Redcon's compact text *itself* would shrink it
   another ~40%. Most of that is repeated literals (file paths, line
   number prefixes, bullet markers). This suggests format tricks (V31,
   V32, V40) are still under-exploited in the compact serialisation.

3. The negative-headroom fixtures (`go_test`, `pip_install_typical`,
   `cargo_test`, `ls`, `docker_build_typical`, `npm_test_jest`, `tree`)
   are below the codec floor, which is only possible because Redcon is
   lossy. They are at or past the rate-distortion frontier for the lossy
   policy declared by their must-preserve-patterns. Further compression
   on those would have to relax the must-preserve set or add ULTRA-only
   tricks. **Time spent tightening these compressors is wasted.**

4. cl100k bytes-per-token ratio is remarkably stable on Redcon-shaped
   text: raw mean 2.93, compact mean 2.65. The compact ratio is *lower*
   (more tokens per byte) because compression strips whitespace runs and
   exposes more BPE-merge boundaries. This is the calibration the
   prompt asked for; per-schema rates fit in a 12-line table.

## Disqualifiers / why this might be wrong

1. **Lossy vs lossless gap is the whole point**: Redcon being below the
   codec floor on small fixtures isn't a bug, it's compression of *facts*
   not *symbols*. The headroom signal is therefore informative only on
   large fixtures with redundant structure; on small ones it just confirms
   "we already win". A naive consumer could over-trust it. Mitigation:
   ship the calibrated floor as a *bound* (with a clear "lossless lower
   bound; lossy methods can beat it") and not as a target.
2. **Calibration ratio is schema-dependent**: pytest output uses ascii box
   characters and very long underscores (5.57 B/tok); diff output uses
   short identifiers (2.44 B/tok). A single global ratio mis-attributes
   tokens by ~2x for some schemas. Mitigated by the per-schema table, but
   small-population schemas (npm_test, cargo) have only one fixture and
   thus a noisy calibration. Need >=3 fixtures per schema for a stable
   ratio.
3. **Already in spirit done by the M9 quality harness**: the `reduction_pct`
   and per-tier benchmarks already tell humans "git_diff has slack". The
   K-proxy adds *how much absolute slack remains*, which is new, but is
   not a new compressor and so does not move the headline numbers. By
   the BASELINE definition of "breakthrough" (>=5pp on COMPACT across
   compressors), this is a research-tooling contribution, not a
   breakthrough.
4. **Brotli q=11 is non-portable in CI**: brotli's encoder has had
   length-by-version drift historically. We'd need to pin and document
   the version; otherwise a CI host on a different brotli ABI would
   produce different `headroom_tokens`, which would alarm developers.
   Stdlib-only fallback (gzip/lzma/bz2) is portable but on this corpus
   yields a 2-3x looser bound, weakening the signal.
5. **K-proxy ensembles are coarse**: the LZ family all model the same
   things (local repetition, short-range Markov). Adding more LZ-family
   coders does not tighten the bound much. Genuine improvement would
   need a model-specific coder (e.g., a CFG coder for diff hunks or
   a tokenizer-aware arithmetic coder), which is V05 / V06 territory.

## Verdict

- Novelty: **medium** (the per-call codec-floor *telemetry* is new for
  Redcon; ensemble-min as a K-proxy is a textbook technique. As a
  research-tool it is novel, as a compression idea it is not).
- Feasibility: **high** (one optional dep, ~250 LOC, off-default flag,
  no determinism risk if codec versions are pinned).
- Estimated speed of prototype: **1 day** for the telemetry + bench column;
  **2-3 days** including the calibration CLI and per-schema table refresh.
- Recommend prototype: **conditional-on-X**, where X = "we are about to
  invest in another round of COMPACT compressor tightening". The headroom
  signal is most valuable as a *triage tool* for that work: it will
  redirect effort from `cargo_test`/`ls`/`pip_install` (already at floor)
  to `git_diff_huge`/`grep_massive`/`ruff_typical`/`mypy_large` (80-98%
  slack remaining). If no such investment is planned, skip; the existing
  reduction% column carries enough signal for normal regression watch.
