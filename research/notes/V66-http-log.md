# V66: HTTP access log compressor (NCSA / combined)

## Hypothesis

When an agent inspects nginx or apache access logs (`tail -f /var/log/nginx/access.log`,
`cat access.log`, `awk '...' access.log`), the raw stream is one of the
single highest-redundancy command outputs the agent ever sees: most rows are
routine 2xx GETs that share almost identical structure, the per-row
high-entropy fields the agent actually wants are status, path, and latency,
and the "interesting" subset (5xx errors, slow requests, top hot paths) is
typically <1% of rows. A schema-aware compressor that emits a status-code
histogram, top-N paths, top-N referers, error-paths sub-table, and a small
latency percentile triplet should clear 95% reduction at COMPACT and
99%+ at ULTRA on a 1000-line fixture - i.e., it lands above the existing
git-diff number (97.0%) and joins the "ULTRA fits in 1 sentence" club.
The interesting subclaim is that detection is essentially free: argv hits
`tail` / `cat` / `awk` over a `*access*.log*` path, with a one-regex
content-sniff fallback.

## Theoretical basis

A combined-log line has the schema

    line = host SP "-" SP "-" SP "[" date "]" SP "\"" method SP path SP proto "\""
           SP status SP bytes (SP "\"" referer "\"")? (SP "\"" ua "\"")? (SP rt)?

with status in a small alphabet (~10 distinct codes typical, actually 6 in
99% of traffic), method in {GET, POST, PUT, DELETE, HEAD, OPTIONS, PATCH}
(7 symbols), proto effectively constant per-server, and host/date/bytes/ua
high-entropy but agent-irrelevant.

Empirical token cost per line under cl100k for a representative entry

    192.168.1.42 - - [25/Apr/2026:09:12:33 +0000] "GET /api/v1/users/12345 HTTP/1.1" 200 2387 "https://example.com/dashboard" "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0 Safari/537.36"

is roughly 55-65 tokens (path + UA dominate). For a 1000-line sample,
raw_tokens ~= 55,000 to 65,000.

The compact form's information content, in symbols:

    H_status   = -sum p_i log p_i  with i over (200, 301, 302, 304, 404, 500, ...)
                ~  log_2(6 codes)   = 2.6 bits  (one line in the output)
    H_paths    bounded by min(|distinct paths|, 20) entries, each path of
                avg ~12 tokens ~= 240 tokens
    H_methods  ~ log_2(7) = 2.8 bits  (one line)
    H_refs     <= 5 entries * ~10 tokens = 50 tokens
    H_errors   <= 10 entries * ~12 tokens = 120 tokens
    H_latency  3 numbers * ~3 tokens = ~10 tokens

Total compact-tier output ceiling:

    T_compact <= 240 + 50 + 120 + 10 + 30 (headers) = ~ 450 tokens

Reduction:

    R = 1 - T_compact / T_raw
      = 1 - 450 / 60000
      = 99.25%

Even doubling every estimate to be conservative (T_compact = 900) the floor
is ~98.5%. This is comfortably above the ULTRA-tier floor (>=70%) and the
COMPACT-tier floor (>=30%).

Why it works: access logs are an extreme case of a *high-cardinality but
low-mutual-information* stream - per-row entropy is high (the host IP has
tens of bits) but the entropy *the agent extracts* is dominated by the
status histogram (2.6 bits across all rows, not per row) and the top-K path
list. The classical compressors work on whole rows, so they pay tokens for
ua/host/date/proto repeatedly; a schema-aware compressor charges them once
each as a column-wise summary.

Latency percentiles. Nearest-rank percentile on n samples is

    p_k = sort(values)[ceil(k * n / 100) - 1]

which is deterministic, O(n log n), no interpolation needed (so no
floating-point order-of-operations issues across architectures). T-digest
(V53) would give a streaming alternative with O(1) memory; for the eager
parser nearest-rank is sufficient and exactly reproducible.

## Concrete proposal for Redcon

New compressor module `redcon/cmd/compressors/http_log_compressor.py`
(sketch already written). New canonical type `HttpLogResult` in
`redcon/cmd/types.py`. Lazy registration in
`redcon/cmd/registry.py::_bootstrap_lazy()` via a fresh `_is_http_log`
predicate.

API outline:

```python
class HttpLogCompressor:
    schema = "http_log"

    def matches(self, argv):
        # argv hit: tail / cat / less / awk over an *access*.log
        if not argv: return False
        if argv[0] in {"tail", "cat", "less", "awk"}:
            return any("access" in a and ".log" in a for a in argv[1:])
        return False

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_http_log(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        return CompressedOutput(
            text=_format(result, level),
            level=level, schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=estimate_tokens(_format(result, level)),
            must_preserve_ok=verify_must_preserve(...),
            truncated=False, notes=ctx.notes,
        )
```

Compact output shape (sketched in the file, summary here):

```
http_log: 1000/1000 rows, 47 err (4.7%), bytes=12834221
latency p50=42ms p90=180ms p99=1.20s
status: 200:912, 301:14, 304:18, 404:32, 500:9, 502:6, 503:0, 504:0
method: GET:870, POST:104, HEAD:18, PUT:8
top paths:
312 /api/v1/users
198 /api/v1/orders
...
errors by path:
9x5xx 0x4xx /api/v1/orders
0x5xx 18x4xx /robots.txt
top referers:
240 https://example.com/dashboard
...
```

Detection wiring (registry):

```python
def _is_http_log(argv):
    if not argv: return False
    if argv[0] in {"tail", "cat", "less", "awk"}:
        return any("access" in a and ".log" in a for a in argv[1:])
    return False
```

Optional content-sniff fallback in `pipeline.compress_command` only when
`detect_compressor(argv)` returns `None` and the first non-blank line of
`raw_stdout` matches the combined-log regex. This is opt-in to avoid
slowing the existing miss path on every command.

Must-preserve patterns: the top-1 path string (so the most-trafficked
endpoint round-trips) and the literal token "5xx" or status-code digits
present in the histogram for any code that appears in the raw input. Both
hold trivially under the proposed compact format.

## Estimated impact

- Token reduction:
  - On a 1000-line nginx fixture with combined-log + `$request_time` on:
    raw ~= 55-65k tokens, compact ~= 350-500 tokens, **>= 98% reduction**.
  - On a smaller 100-line fixture: raw ~= 5.5-6.5k tokens, compact ~=
    200-350 tokens, **~ 94-96% reduction**.
  - On a 50-line "noisy debug pull" fixture: header overhead dominates,
    closer to ls's 33%; raw ~= 3k tokens, compact ~= 200-300, ~ 90%.
  - Compact-tier reduction sits between git-diff (97%) and find (81%); it
    should land near git-diff.
- Latency: parse is O(n) regex match per line. 1000-line parse is < 50 ms
  on a modern laptop. Cold start: registry already lazy, so adding this
  compressor costs nothing at import time.
- Affects: only adds a new compressor + new type. No existing compressor's
  code changes. Cache-key path is unaffected (argv-based; the new matcher
  is additive). Quality harness gains one more golden fixture and a
  must-preserve regex tuple.

## Implementation cost

- Lines of code: sketch is ~250 LOC including docstrings + formatter for
  three tiers + nearest-rank percentile helper. Production version with
  a `tests/` corpus and golden fixture: ~400-500 LOC total.
- New runtime deps: zero. Stdlib `re`, `dataclasses`. Does not break the
  "no required network / no embeddings" rule.
- Risks:
  - Determinism: nearest-rank percentile (no float interpolation) is
    bit-exact. `dict` iteration is insertion-order in CPython 3.7+; the
    formatter sorts every histogram before emitting, so output is stable.
  - Robustness: combined-log regex must reject binary garbage, truncated
    mid-line, 5000 newlines, random word spam. Mitigation: single-pass
    regex, lines that fail the match are silently skipped and counted in
    `rows_total - rows_parsed`. The header line carries this delta so the
    agent can tell that 80% of rows didn't parse.
  - Must-preserve: the top-1 path is always retained; 5xx codes survive in
    the status histogram. ULTRA tier may drop both per the BASELINE rule
    that ULTRA is exempt from must-preserve.
  - Detection false positives: argv hit on `cat foo-access-control.log`
    would fire incorrectly. Mitigation: also content-sniff for the
    combined-log regex on the first parseable line; if it fails, fall
    back to the generic listing/no compressor path.

## Disqualifiers / why this might be wrong

1. **Agents rarely tail access logs in coding tasks**. Redcon's bread and
   butter is `git diff`, `pytest`, `grep`. An access-log compressor is
   real but probably <1% of traffic in the IDE-coding personas Redcon
   targets. A 99% reduction on 1% of traffic is a smaller global win than
   a 5pp reduction on git-diff. So even if it lands, it's not a
   *breakthrough* per the BASELINE definition; it's a point-feature.
2. **Custom log formats break the regex**. `log_format` in nginx is
   user-defined. Combined is the default and the most common, but JSON
   logs (V65 territory) and shop-specific formats (extra fields, missing
   referer, latency in microseconds) won't match. The proposal handles
   the optional `$request_time` tail and a missing-referer case but
   anything beyond that needs either a config probe (impossible in the
   compress path) or accepting that ~10-20% of real-world logs fall
   through to the listing/raw path. Not catastrophic but a real
   coverage ceiling.
3. **Already partly covered by the log-pointer tier**. BASELINE notes:
   "when raw output > 1 MiB, spill full bytes to .redcon/cmd_runs/<digest>.log,
   emit pointer + tail-30." For *huge* access logs that path already
   gives 99%+ reduction without parsing. V66's win zone is the
   100-row to 100k-row band where log-pointer isn't triggered but
   structure-aware compression dominates plain tail-30. Smaller than
   it sounds.
4. **JSON-logging is the modern direction**. Cloud-native services log
   JSON (one-event-per-line dict), not combined. V65 (JSON-log
   compressor) is the more general primitive; V66 is its low-tech
   sibling. If V65 lands first and is good enough, V66 may be
   redundant for new infrastructure and only useful for legacy nginx
   dumps.
5. **`tail -f` is streaming, not eager**. The `Popen`-based runner reads
   bounded streams; for `tail -f` it will block until kill / timeout.
   This compressor is most useful on `tail -n 1000 access.log` and
   `cat access.log | head -10000` patterns. Live tail semantics
   (anytime-algorithm tier; V57) is a separate problem.

## Verdict

- Novelty: **medium** (well-known schema, but not implemented in Redcon;
  ranks on existing-frontier tools)
- Feasibility: **high**
- Estimated speed of prototype: **1 day** for the compressor, fixture,
  golden test, registry wiring, and quality-harness must-preserve list
- Recommend prototype: **conditional-on-traffic** - ship if telemetry
  ever shows agents running `tail`/`cat` on `*.log` files in the field
  at >1% of `redcon_run` calls, or as part of a "sysadmin-flavour"
  compressor pack alongside V67 (k8s events), V68 (CI logs), V70
  (profiler). Standalone, the BASELINE-defined breakthrough bar (>=5pp
  across multiple compressors) is not met because this is a single new
  compressor that doesn't compose with the existing 11. As a building
  block toward an "ops-side" compressor cluster (V65/V66/V67/V68
  shipped together), it's worth doing.
