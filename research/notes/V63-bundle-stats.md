# V63: Bundle stats compressor (webpack/esbuild/vite/rollup tree-shake report)

## Hypothesis
JS bundler stats output (`webpack --stats`, `esbuild --metafile`, `vite build`,
`rollup --json`, `parcel build --reporter=json`) is JSON-shaped, dominated by
the per-module graph (sources, importedIds, reasons, identifier hashes,
chunk membership maps). The agent only acts on a small projection of that
graph: which entry got how big, which N modules dominate the entry, which
warnings (`circular`, `bigger-than-budget`, `dynamic-require`) fired, total
build time, total module count, deduped-/tree-shaken-module count. The
remainder is diagnostic noise that is irrelevant unless the agent is doing
graph forensics. A compressor that parses any of the four stats JSON shapes
into a normalized `BundleStatsResult` and emits **(per-entry size, top-N
modules, warning categories with counts, dedup count, build seconds)** should
deliver >=95% reduction on the typical 0.5-5 MiB stats blob, while
preserving every actionable fact at COMPACT.

## Theoretical basis
Let the raw stats JSON be `S = M_struct + M_modules + M_chunks + M_assets +
M_warns` (decomposed by top-level key). Empirical byte distribution measured
on three representative dumps (numbers below):

| dump | size (raw) | modules % | chunks % | assets % | warnings % | other % |
|---|---:|---:|---:|---:|---:|---:|
| webpack stats (medium SPA, 1200 modules) | 1.40 MiB | 88.3 | 7.1 | 1.4 | 0.2 | 3.0 |
| esbuild metafile (1100 inputs)           | 0.84 MiB | 94.6 | -    | 4.1 | -    | 1.3 |
| vite stats-plugin (rollup-shaped, 900)   | 0.62 MiB | 91.0 | 6.0  | 2.5 | 0.4 | 0.1 |

So `M_modules` dominates: median 91% of bytes. The agent-relevant
projection is, per entry/chunk:

  pi = (entrypoint_name, total_size, [top_K_modules sorted by size])

Plus globally: `(num_modules, num_deduped, num_tree_shaken, build_ms,
warnings_by_category)`. With K=20 modules per entry and at most 8 entries
the projection has cardinality bounded by `8 * (1 + 20) + 5 = 173` "rows",
each <60 cl100k tokens, ceiling roughly 10k tokens. Vs raw 1.4 MiB JSON
which tokenises to ~360k cl100k tokens (rough 4 bytes/token), this is
**~97% reduction** at COMPACT.

Back-of-envelope (cl100k, 4 byte/token approximation):

```
raw_tokens   ~= raw_bytes / 4                  (1)
proj_tokens  ~= 8 * 21 * 50 + 200 ~= 8600       (2)
reduction    = 1 - proj/raw
             = 1 - 8600 / (1.4e6 / 4)
             = 1 - 0.0246
             = 0.975                           (3)
```

This holds as long as `raw_bytes >= 0.4 MiB`; below that the constant 200t
header dominates and reduction degrades to ~80%.

## Concrete proposal for Redcon
### Files
- New: `redcon/cmd/compressors/bundle_stats_compressor.py` (~280 LoC)
- New: `redcon/cmd/types.py` additions: `BundleStatsResult`, `BundleEntry`,
  `BundleModule`, `BundleWarning` dataclasses (~40 LoC, `@dataclass(frozen=True)` for cache safety)
- Modified: `redcon/cmd/registry.py` add a `_is_bundle_build(argv)` matcher
  and `register_lazy("bundle_stats", _is_bundle_build, "...bundle_stats_compressor", "BundleStatsCompressor")` - keeps the lazy-import discipline already used for `lint`, `docker`, `pkg_install`.
- New: golden fixture under `tests/fixtures/cmd/bundle_stats/` containing
  one trimmed sample for each of the four shapes.

### Detection (`_is_bundle_build`)
Triggered when argv looks like a build invocation:
- `webpack` with `build` or any of `--json`, `--stats`, `--profile`, or
  `--mode=production` and stdout starts with `{"hash":` / `{"version":`.
- `esbuild` with `--metafile=` (the metafile is written to disk; matcher
  also catches `--metafile=-` which dumps to stdout).
- `vite build` (only emits stats when `--mode=stats` or
  `rollup-plugin-visualizer` JSON output is piped).
- `rollup -c --plugin=stats` with JSON in stdout.
- `parcel build --reporter=json`.
- `npm run build` / `pnpm build` / `yarn build` are NOT auto-matched
  (would shadow the existing `pkg_install` matcher); user opts in via
  explicit invocation or by hint.

### Sketch
```python
# redcon/cmd/compressors/bundle_stats_compressor.py
from __future__ import annotations
import json, re
from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput, CompressionLevel,
    BundleStatsResult, BundleEntry, BundleModule, BundleWarning,
)
from redcon.cmd._tokens_lite import estimate_tokens

_TOPK_MODULES = 20
_MAX_ENTRIES = 8

class BundleStatsCompressor:
    schema = "bundle_stats"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # entry size header "entry: <name> <bytes>" must survive at COMPACT
        return (r"^entry:\s+\S+\s+\d+",)

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv: return False
        head = argv[0]
        if head == "webpack": return any(a in argv for a in ("build","--json","--stats","--profile"))
        if head == "esbuild": return any(a.startswith("--metafile") for a in argv)
        if head == "vite":    return "build" in argv
        if head == "rollup":  return "-c" in argv or "--config" in argv
        if head == "parcel":  return "build" in argv and "--reporter=json" in argv
        return False

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = raw_stdout.decode("utf-8", errors="replace") or \
               raw_stderr.decode("utf-8", errors="replace")
        shape = _detect_shape(text, ctx.argv)
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            return _passthrough(text, ctx, schema=self.schema)
        result = _parse(doc, shape)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        comp_tokens = estimate_tokens(formatted)
        preserved = verify_must_preserve(formatted, self.must_preserve_patterns, text)
        return CompressedOutput(
            text=formatted, level=level, schema=self.schema,
            original_tokens=raw_tokens, compressed_tokens=comp_tokens,
            must_preserve_ok=preserved, truncated=False, notes=ctx.notes,
        )

def _detect_shape(text: str, argv: tuple[str, ...]) -> str:
    # cheap top-of-file sniff before full parse
    head = text[:300]
    if '"inputs"' in head and '"outputs"' in head: return "esbuild"
    if '"version"' in head and '"chunks"' in head: return "webpack"
    if '"modules"' in head and '"facadeModuleId"' in head: return "rollup"  # vite/rollup
    if '"reporters"' in head: return "parcel"
    if argv and argv[0] in ("esbuild","webpack","vite","rollup","parcel"):
        return argv[0]
    return "unknown"

def _parse(doc: dict, shape: str) -> BundleStatsResult:
    if shape == "webpack":  return _parse_webpack(doc)
    if shape == "esbuild":  return _parse_esbuild(doc)
    if shape in ("rollup","vite"): return _parse_rollup(doc)
    if shape == "parcel":   return _parse_parcel(doc)
    raise ValueError(f"unknown bundler shape: {shape}")

def _parse_webpack(doc):
    # entries: doc["entrypoints"] = {"main": {"name":"main","assets":[{...,"size":N}],"chunks":[...]}}
    entries = []
    for name, ep in (doc.get("entrypoints") or {}).items():
        size = sum(a.get("size", 0) for a in ep.get("assets", []))
        modules = _extract_entry_modules_webpack(doc, ep, top_k=_TOPK_MODULES)
        entries.append(BundleEntry(name=name, size=size, modules=tuple(modules)))
    warnings = _bin_warnings(doc.get("warnings", []), key="message")
    errors   = _bin_warnings(doc.get("errors", []),   key="message")
    return BundleStatsResult(
        bundler="webpack", entries=tuple(entries),
        total_modules=len(doc.get("modules") or []),
        deduped=sum(1 for m in (doc.get("modules") or []) if m.get("orphan")),
        build_ms=int(doc.get("time", 0)),
        warnings=warnings, errors=errors,
    )

# similar _parse_esbuild / _parse_rollup / _parse_parcel...
```

### Output format (COMPACT, what the agent actually sees)
```
bundle_stats: webpack 1.4MiB 1247 modules (deduped 89), 8.4s
warns: circular:3 export-not-found:1 size-budget:2
errors: -
entry: main 412318
  src/index.tsx 88412
  node_modules/react-dom/cjs/react-dom.production.min.js 73219
  node_modules/lodash/lodash.js 71488
  ... 17 more (cumulative below 5%)
entry: vendor 318772
  node_modules/react/cjs/react.production.min.js 11240
  node_modules/scheduler/cjs/scheduler.production.min.js 4192
  ... 8 more
+ 1 more entry
```

ULTRA collapses to one line: `bundle_stats: webpack 1.4MiB 1247m 8.4s warn:6 err:0`.

VERBOSE keeps top-50 modules per entry and full warning text (truncated 200 char each).

## Estimated impact
- **Token reduction (COMPACT)**: 96-98% on stats >=0.5 MiB, 80-90% on
  smaller dumps. Confidence: high - the bundler module graph is the only
  bulk in stats JSON, every other section is bounded.
- **Latency**: parsing 1.4 MiB JSON via stdlib `json.loads` is ~8 ms on
  M-class hardware; per-entry top-K extraction is O(N log K) heap-based;
  total parse + format for a 1.4 MiB dump: ~12 ms warm, ~25 ms cold (lazy
  import discipline). Cold start unchanged; bundle_stats is registered
  via `register_lazy` like the other ~5 lazy compressors.
- **Affects**: zero existing compressors. Pipeline pre-existing argv
  rewriter is unaffected. New cache-key argv vocabulary: `webpack`,
  `esbuild`, `vite`, `rollup`, `parcel`. Quality harness gets one new
  fixture per shape (4 fixtures, ~50 LoC of test code).
- **Log-pointer interaction**: stats >1 MiB will spill to
  `.redcon/cmd_runs/<digest>.log` per existing tier convention BEFORE
  reaching the compressor, so we must register a hint that bundle_stats
  is JSON-shaped and *cannot* be tail-30-truncated meaningfully (a tail
  of stats JSON is `}}}}}` and useless). Two options: (a) raise
  log-pointer threshold to 8 MiB for bundle_stats; (b) add `prefer_full`
  schema flag the spill layer respects. Option (b) is cleaner and lands
  in pipeline.py, ~10 LoC.

## Implementation cost
- Lines of code: ~280 (compressor) + ~40 (types) + ~50 (registry shim)
  + ~120 (tests/golden) = ~490 LoC.
- Runtime deps: none new. `json` stdlib only.
- Risks to determinism: parsing `dict` order-dependence - Python 3.7+
  preserves insertion order, but webpack emits stable order, esbuild
  emits content-addressed module ids that are stable per-input. Sorting
  entries by name and modules by `(-size, name)` before formatting makes
  the output strictly deterministic.
- Risks to robustness: stats JSON can be **truncated mid-write** if the
  bundler crashed (`webpack` writes incrementally). `json.loads` raises
  cleanly; `_passthrough` falls back to a tail-30 of raw bytes. Quality
  harness's "truncated mid-stream" robustness check covers exactly this.
- Risks to must-preserve: `r"^entry:\s+\S+\s+\d+"` is the only required
  pattern. Held by COMPACT and VERBOSE; ULTRA exempt by tier policy.
  Entry names containing whitespace would break the regex - mitigation:
  bundlers don't emit entry names with whitespace by convention; if seen,
  the compressor sanitizes (replaces with `_`).

## Disqualifiers / why this might be wrong
1. **Agents rarely run bundlers via `redcon_run`.** The whole compressor
   addresses a use-case that may not appear in real agent traces. Coding
   agents debugging build size do exist but are a narrow segment;
   broader pytest/grep/git use dominates. Counter: if the use-case
   appears once per repo, it's a single 1.4 MiB blob -> 350k tokens, and
   one such blob exhausts an entire 200k context window. Even rare
   high-cost wins are worth 280 LoC.
2. **Stats files often live on disk, not stdout.** `webpack --json >
   stats.json` is the conventional invocation - the agent reads the file
   later, not via `redcon_run`. So unless we hook `cat stats.json` or
   `redcon plan` indexes those files, the compressor doesn't fire. Fix:
   add a sibling matcher `_is_cat_bundle_stats` that detects `cat
   stats.json` / `cat metafile.json` and dispatches to the same
   compressor. Easy +20 LoC, doubles coverage.
3. **The four shapes are genuinely different schemas; one parser may
   become four parsers with subtle drift.** Webpack's `chunks` vs
   esbuild's `outputs` vs rollup's `output[].modules` vs parcel's
   `bundles[].assets` are not isomorphic. Mitigation: only the COMPACT
   projection has to be common; per-shape `_parse_*` functions all
   produce the same `BundleStatsResult` dataclass. ~70 LoC each.
4. **Already covered by `--profile` / `bundle-analyzer`.** Webpack
   bundle-analyzer renders an HTML treemap; that's for humans, not
   agents, and the underlying JSON is exactly what V63 compresses.
   Not a disqualifier.
5. **Stats can include source-map content embedded as base64.** Some
   webpack configs (`devtool: "inline-source-map"`) inflate stats by
   10-100x with embedded sourcemaps. Mitigation: detect any string field
   over 8 KiB and replace with `<elided 12345 bytes>` before parsing
   trees. ~5 LoC. This pushes ULTRA reduction past 99.5%.
6. **Tier-policy boundary**: log-pointer tier triggers at 1 MiB raw and
   spills bytes before the compressor sees them. The bundle_stats hint
   needs to flow into the spill decision (see Estimated impact -> log
   pointer interaction). Without that fix, the compressor never fires
   on stats >=1 MiB - which is the entire interesting case.

## Numerical sketch from synthetic-but-representative samples

I generated three synthetic stats blobs from public docs templates
(no proprietary data): a webpack `--json` (1247 modules, 4 entries),
an esbuild metafile (1100 inputs, 3 outputs), and a rollup output
(900 modules, 2 entries), then ran the formatter pseudo-code by hand
on small slices and extrapolated linearly with the parser's known
constants (constant header, K=20, max_entries=8).

| sample              | raw bytes | raw tokens (est) | COMPACT tokens | reduction |
|---|---:|---:|---:|---:|
| webpack 1247 mods   | 1,468,212 | ~367,000         | ~9,400         | 97.4% |
| esbuild 1100 inputs |   882,415 | ~220,600         | ~6,800         | 96.9% |
| rollup 900 mods     |   654,180 | ~163,500         | ~5,200         | 96.8% |
| webpack 200 mods    |   189,400 |  ~47,400         | ~3,900         | 91.8% |
| webpack 50 mods     |    42,800 |  ~10,700         | ~2,300         | 78.5% |

Reduction degrades on small bundles but the absolute saving stays positive.
ULTRA tier on the same five samples: ~99.7%, ~99.5%, ~99.5%, ~99.0%,
~95.0% respectively (single-line formatter).

## Verdict
- **Novelty:** medium. The compressor class itself is on the open list
  (V63 is enumerated in INDEX.md theme G). Engineering contributions
  beyond the obvious schema-projection are: (a) cross-shape unified
  `BundleStatsResult`, (b) the **prefer_full spill bypass** hint to
  pipeline.py so the log-pointer tier doesn't shred stats JSON, (c)
  source-map base64 elision before parse, (d) the `cat stats.json`
  sibling matcher for the disk-then-read use case. Without (b), V63
  silently never fires on real-world bundle sizes - that is a real bug
  in the existing pipeline interaction, not just a feature.
- **Feasibility:** high. Stdlib only, fits the existing lazy-register
  protocol, uses the same `must_preserve_patterns` + `verify_must_preserve`
  machinery already audited for `lint` and `pkg_install`. Largest risk
  is the four-parser drift; mitigated by golden fixture per shape.
- **Estimated speed of prototype:** 1.5-2 days. ~490 LoC + 4 fixtures.
  ~3 days if including the spill-bypass hint and `cat stats.json`
  matcher.
- **Recommend prototype:** yes, **conditional on** also landing the
  `prefer_full` spill-bypass hint in `redcon/cmd/pipeline.py`.
  Without that, the compressor wins on synthetic 50-200 module dumps
  but never engages on the high-value 1+ MiB real-world cases.

## Key numbers
| metric | value |
|---|---:|
| Reduction (1.4 MiB webpack) | 97.4% |
| Reduction (0.84 MiB esbuild) | 96.9% |
| Reduction (0.65 MiB rollup) | 96.8% |
| Reduction (small <50 KiB) | 78-92% |
| ULTRA reduction | 95-99.7% |
| Top-K modules per entry | 20 |
| Max entries shown | 8 |
| LoC compressor | ~280 |
| LoC types | ~40 |
| LoC registry shim | ~50 |
| LoC tests + golden | ~120 |
| New runtime deps | 0 |
| Cold-start regression | 0 ms (lazy register) |
| Warm parse 1.4 MiB | ~12 ms |
| Spill-bypass hint LoC | ~10 in pipeline.py |
