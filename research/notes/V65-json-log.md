# V65: JSON-log compressor - mine schema, transmit table-shaped

## Hypothesis

Most production JSON-line logs (ndjson, structured logs out of `pino`,
`bunyan`, `loguru`, `slog`, `serilog`, the AWS CloudWatch / GCP Cloud
Logging exports) are not really "JSON" in any deep sense. They are a
*relation* in disguise: every line is a record of the same schema, and
the JSON envelope around each record is a per-record-repeated tax of
keys, quotes, colons, braces. Mine the dominant schema once, then emit
the records as a header plus positional rows (one CSV-ish line per
record). On a 200-line ndjson sample with the typical 4-key envelope
`{ts, level, msg, trace_id}` the per-record overhead drops from
"`"ts":"...","level":"...","msg":"...","trace_id":"..."`" to
"`<ts>,<level>,<msg>,<trace_id>`" - roughly 18 bytes of literal key
syntax saved per row. Across 200 rows that is ~3.6 KB of pure framing,
which on cl100k tokenises at ~1100 tokens of pure tax. The claim: a
mined-schema columnar layout will reduce a representative ndjson sample
by **>=55% at COMPACT and >=75% at ULTRA** while keeping every row's
field values byte-recoverable, with deterministic same-input-same-output.

This is BASELINE-frontier-relevant: there is currently no JSON-log
compressor. The closest neighbours (`pytest`, `lint`, `grep`) all parse
text, not structured JSON streams. A JSON-log compressor adds a new
schema to the registry and composes with the log-pointer tier
(commit a44993b) when raw output >1 MiB.

## Theoretical basis

Treat a 200-line ndjson stream as a sequence of records
`R = (r_1, ..., r_n)` over some key space `K`. For each line
`r_i: K_i -> V_i`, where `K_i subset K`. Define the dominant schema
`S = { k in K : freq(k) / n >= tau }` for threshold `tau` (here 0.8).
Records that match the schema exactly (`K_i = S`) we call *conformant*;
the rest are *outliers*.

The byte cost of a conformant record in the original JSON envelope is

    cost_json(r) = 2 (braces) + sum_{k in S} ( 4 + |k| + |v_k| ) - 1

(each key contributes `"k":` = `|k| + 3 bytes`, each value contributes
`"v"` = `|v| + 2 bytes`, separator `,` between fields = 1 byte; the
`-1` removes the trailing comma; numbers/bools save the 2 quote bytes
but we'll absorb that into the `|v|` term as a small constant).

The byte cost of the same record in the table form is

    cost_table(r) = sum_{k in S} |v_k| + (|S| - 1)   [single-byte separators, no keys]

Per-record savings:

    delta = cost_json - cost_table
          = 2 + sum_{k in S} ( 4 + |k| ) - (|S| - 1)
          = 3 + |S| * 4 + sum_{k in S} |k| - |S|
          = 3 + 3|S| + sum_{k in S} |k|.

For the canonical 4-key envelope `{ts, level, msg, trace_id}`,
`sum |k| = 2 + 5 + 3 + 8 = 18` and `|S| = 4`, so
`delta = 3 + 12 + 18 = 33 bytes per row`. Over 200 rows that is
**6600 bytes** of *envelope tax* removed before any value-side
compression. Per cl100k empirical ratio of ~3.5 bytes/token on
ASCII-heavy framing, that is **~1880 tokens** saved on framing alone.

Outliers are billed at full JSON cost. Let `p` = conformant fraction.
Total reduction in framing tokens is

    R_framing = p * delta_tokens / total_tokens.

At p=0.95 (typical for production logs after a stable schema lands),
R_framing alone is ~25% on a small log, dwarfing what header overhead
costs us. The header itself is `O(|S|)` and amortised across `n`
rows: `header_cost / n -> 0`.

There is a deeper bound. The relation interpretation lets us reason in
terms of column-store entropy. If each column has marginal entropy
`H(C_k)`, the per-record information-theoretic floor is
`sum_k H(C_k)` (assuming columns are independent; the bound is loose
because in real logs `level` and `msg` and `trace_id` share mutual
information with `ts` via session). The JSON envelope adds
`H_envelope = sum_k |k| * 8` bits of *deterministic* (zero-entropy)
overhead per row, all of which is recoverable from the schema header.
Removing it is lossless - this is the "compress what is constant"
principle from Shannon, and it does not trade against any agent-visible
fact.

## Concrete proposal for Redcon

### New file: `redcon/cmd/compressors/json_log_compressor.py` (~180 LOC)

Pipeline:

1. Detect: argv is `cat foo.log`, `tail -n N foo.ndjson`, `tail -f`-style
   already drained, `journalctl -o json`, `kubectl logs --output=json`,
   or stdin contains lines that mostly start with `{` and end with `}`.
   The argv predicate is conservative; the strong signal is content.
   Add a content sniffer in `detect_compressor` that peeks the first
   ~8 KiB and counts `lines_starting_with_{` / `total_lines >= 0.8`.
2. Parse: line-by-line `json.loads`. Each line that fails to parse is
   appended to `outliers: list[str]` (raw text, capped at `MAX_OUTLIERS`).
   Each successful parse contributes its top-level dict.
3. Mine schema: iterate dicts once, count key frequencies. Schema is
   `tuple(sorted(k for k, c in counts.items() if c / n >= 0.8))`. The
   `sorted` is for determinism; alternatively keep first-seen order
   (also deterministic) for human readability - prefer first-seen.
4. Type hint per column (cheap): if every value of column `k` parses as
   ISO-8601, mark `k` as `:ts`; if every value is in
   `{"DEBUG","INFO","WARN","WARNING","ERROR","FATAL","CRITICAL"}` mark
   `:level`; else `:str`. Used only for header annotation, not rewriting
   the values yet (V14 type-collapsing is the next vector that would
   take advantage).
5. Format. ULTRA: schema header + per-level histogram + per-trace_id
   count + first/last ts + outlier count. COMPACT: header line listing
   keys with type tags, then one CSV-ish row per conformant record
   using a chosen separator (see "separator" subsection), then a tail
   block listing outliers. VERBOSE: re-emit raw lines with whitespace
   normalised.

Sketch:

```python
class JsonLogCompressor:
    schema = "json_log"
    SCHEMA_THRESHOLD = 0.8
    MAX_OUTLIERS = 20
    must_preserve_patterns = ()  # see Disqualifier 1

    def matches(self, argv):
        if not argv:
            return False
        head = argv[0]
        if head in {"cat", "tail", "head"}:
            return any(a.endswith((".log", ".ndjson", ".jsonl")) for a in argv[1:])
        if head == "journalctl" and "-o" in argv:
            i = argv.index("-o")
            return i + 1 < len(argv) and argv[i+1] in {"json", "json-pretty"}
        if head == "kubectl" and "logs" in argv:
            return "--output=json" in argv or "-o=json" in argv
        return False

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = raw_stdout.decode("utf-8", "replace")
        records, outliers = _parse_lines(text)
        schema, types = _mine_schema(records, self.SCHEMA_THRESHOLD)
        result = JsonLogResult(records=records, outliers=outliers,
                               schema=schema, types=types)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        ...

def _mine_schema(records, tau):
    counts = {}
    order = []
    for r in records:
        for k in r:
            if k not in counts:
                order.append(k)
            counts[k] = counts.get(k, 0) + 1
    n = max(len(records), 1)
    schema = tuple(k for k in order if counts[k] / n >= tau)
    types = {k: _infer_type(records, k) for k in schema}
    return schema, types

def _format_compact(result):
    lines = []
    header = "# " + " | ".join(f"{k}:{result.types[k]}" for k in result.schema)
    lines.append(header)
    for r in result.records:
        if all(k in r for k in result.schema):
            row = _SEP.join(_render(r[k]) for k in result.schema)
            lines.append(row)
        else:
            # missing-key conformant record; emit '-' for absent fields
            row = _SEP.join(_render(r.get(k, "-")) for k in result.schema)
            lines.append(row)
    if result.outliers:
        lines.append(f"--- {len(result.outliers)} outliers ---")
        lines.extend(result.outliers[:result.MAX_OUTLIERS])
    return "\n".join(lines)
```

### Separator choice

cl100k merges differ for `,`, ` | `, tab, and ``. Empirical
single-token cost per separator instance:

- `,` between two short ASCII tokens: usually merges into the
  surrounding token (effectively 0-1 tokens).
- ` | `: ~1 token always (merges as ` |`).
- `\t`: ~1 token, never merges with letters.
- `` (US, ASCII record separator): ~1 token, ugly, agents may
  distrust it.

Pick `,` for compatibility with downstream agents that may want to
re-parse. If a value contains `,`, fall back to a `\t` row with the
schema header re-emitted as `# tab-separated`. Choose row-wide once
based on whether any value contains `,` to keep determinism trivial.

### Registry

Add a lazy entry in `redcon/cmd/registry.py::_seed_default_registry`:

```python
register_lazy("json_log",
    matcher=_json_log_argv_matcher,
    module_name="redcon.cmd.compressors.json_log_compressor",
    class_name="JsonLogCompressor")
```

### `types.py` additions

```python
@dataclass(frozen=True, slots=True)
class JsonLogResult:
    records: tuple[dict, ...]      # one dict per parsed line
    outliers: tuple[str, ...]      # unparsed raw lines
    schema: tuple[str, ...]        # mined dominant keys
    types: dict[str, str]          # k -> ":ts" / ":level" / ":str" / ":num"
```

### Integration with log-pointer tier

When `len(raw_stdout) > 1 MiB`, the existing log-pointer path already
spills bytes to `.redcon/cmd_runs/<digest>.log` and emits a tail-30
pointer. The JSON-log compressor should run *before* spillover when
the log fits, and *after* spillover on tail-30 when it does not. Tail-
30 of a structured log is itself ndjson, so the same compressor still
works on the tail. No special-casing needed.

## Estimated impact

- Token reduction: empirical envelope-tax math gives ~1880 tokens out
  of ~6600 raw on a 200-line {ts, level, msg, trace_id} sample. Adding
  per-row whitespace and bracket savings, expected COMPACT reduction is
  **~55-65%**. ULTRA (counts + histograms only) reaches **~92-97%**, in
  line with `git_status` and `pytest` ULTRA. Below the 30% COMPACT
  floor never; above 70% ULTRA floor on any non-trivial sample.
- Latency: one `json.loads` per line. On 200 lines that is ~0.4 ms;
  on 1e5 lines (just under spillover) ~0.2 s. Both are dominated by
  subprocess wall-time. No regex backtracking risk because we never
  regex the whole blob - we split by newlines and let `json.loads`
  handle each line.
- Affects: new compressor only. No change to existing scorers,
  cache, tokenizer, or quality harness machinery (just adds one more
  schema for the harness to fuzz).
- Composes with: V14 (type-collapsing) - once columns have type tags,
  V14 can elide redundant literals (e.g. `level=INFO` 197 times -> drop
  the column for that row range). V53 (t-digest) on numeric latency
  columns. V60 (rolling-hash dedup) on `msg` column when one error
  spams. V67 (k8s events) is a strict generalisation of this vector
  for one specific structured-event schema.

## Implementation cost

- Lines of code: ~180 in the compressor + ~10 in `types.py` + ~5 in
  the registry + ~30 in golden fixtures + ~20 in argv detection
  predicate = **~245 LOC** including tests.
- New runtime deps: **none**. `json` is stdlib.
- Risks to determinism: low. `json.loads` is deterministic. Schema
  mining is deterministic given first-seen key order. The only
  non-obvious source of non-determinism would be `dict` iteration
  order, which has been insertion-ordered since CPython 3.7 (and is
  spec-guaranteed since 3.7). Safe.
- Risks to robustness: harness already fuzzes 5000 newlines and binary
  garbage. With binary garbage we get zero conformant records, schema
  is empty, formatter falls through to "json_log: 0 records, N
  outliers" plus the outlier tail capped at MAX_OUTLIERS. Truncated-
  mid-stream gives one trailing partial line that fails `json.loads`
  and lands in outliers. Both safe.
- Risks to must-preserve: the must_preserve_patterns tuple is empty
  by default (see Disqualifier 1). If a downstream caller needs a
  specific trace_id preserved, they can pin VERBOSE.

## Disqualifiers / why this might be wrong

1. **Must-preserve is genuinely hard for arbitrary log content.** Log
   lines can contain anything - sensitive trace IDs, numbers the agent
   actually needs to grep on, error stack frames embedded in `msg`. We
   cannot declare a fixed `must_preserve_patterns` tuple because we do
   not know the content domain. Mitigation: keep patterns empty and
   rely on the COMPACT format being lossless (every conformant record's
   value bytes survive verbatim). At ULTRA we drop per-record content
   in favour of histograms - this is consistent with how `pytest` and
   `lint` ULTRA already behave, and BASELINE explicitly exempts ULTRA
   from must-preserve enforcement.
2. **Real logs have nested objects.** `{ "ctx": { "user_id": 42, "ip":
   "1.2.3.4" } }` does not table-flatten cleanly. We could
   dot-flatten (`ctx.user_id, ctx.ip`) at schema-mine time, but then
   the schema explodes in width and the threshold check at 0.8 starts
   excluding columns. Mitigation: the `_render` helper emits nested
   values as a JSON sub-string, so `ctx` becomes one column whose row
   value is `{"user_id":42,"ip":"1.2.3.4"}`. We lose the per-key
   tabular benefit on nested keys but at least the framing of the
   *outer* envelope still wins. A v2 could recursively mine
   sub-schemas; out of scope for prototype.
3. **Outlier rate may be high.** If the log has 30% conformant and 70%
   outliers (e.g. multi-line stack traces interleaved with structured
   lines), we pay header cost for almost no benefit. Mitigation:
   compute a guard
   `if conformant_fraction < 0.5: fall_back_to_text_compressor`. This
   is testable and deterministic. Note that the popular workflows for
   Python (`logging`) and Java (`logback`) split stack traces across
   many JSON-less lines; those workflows are exactly where this
   compressor shouldn't activate.
4. **Schema mining is online but my proposal is two-pass.** Streaming
   variants (V51 reservoir, V58 adaptive sampling) would prefer a
   one-pass approach. We can do one-pass with a tentative schema that
   gets revised as more lines arrive, but the prototype is happy to
   buffer up to the spillover threshold (1 MiB) and re-pass. Not a
   blocker for V1.
5. **Already partially covered by `_normalise_whitespace`.** No - that
   only collapses `\n{3,}` -> `\n\n`; it does nothing to JSON keys.
   This is a genuine gap.
6. **Detection ambiguity.** Many CLIs print mixed JSON + plain text
   (`docker compose up` with some services in JSON mode, some not).
   The 0.8 conformance threshold protects against this; below 0.8 we
   should not engage. Conservative argv predicate plus content sniffer
   keeps the false-activation rate near zero.

## Verdict

- Novelty: **medium**. The technique (column-store / dictionary-of-keys)
  is textbook from DB / Parquet / Apache Arrow land, and ndjson-to-CSV
  is a standard Unix one-liner. The contribution here is *applying it
  inside a deterministic agent-facing token-budgeting compressor* and
  threading it through Redcon's tier model + log-pointer + quality
  harness. Not a breakthrough by BASELINE's >=5pp-across-multiple bar
  on its own, but a clean addition that brings a previously-unhandled
  output class into the zoo at competitive numbers (~55% COMPACT,
  ~92% ULTRA on representative input).
- Feasibility: **high**. Zero new deps, zero embedding hot-path risk,
  passes the existing quality harness as written.
- Estimated speed of prototype: **1 day**. Compressor + types + registry
  wiring + 3 golden fixtures (4-key envelope, nested-`ctx`, mixed
  outlier-heavy) + harness pass.
- Recommend prototype: **yes**. Adds a 12th compressor at numbers
  comparable to the top half of the existing zoo. Composes with V14
  (type-collapse) for a follow-up bump on numeric-heavy logs and with
  V67 (k8s events) which would inherit the schema-mining core.
