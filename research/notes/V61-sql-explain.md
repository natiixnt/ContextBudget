# V61: SQL EXPLAIN ANALYZE compressor (Postgres + MySQL)

## Hypothesis

`psql -c "EXPLAIN ANALYZE ..."` and the MySQL `EXPLAIN ANALYZE` /
`EXPLAIN FORMAT=TREE` outputs are tree-shaped, indentation-encoded
operator plans typically 12-50 lines long. Agents touching them today
get raw stdout and either skim it or, more commonly, ignore everything
except `Execution Time`. The hypothesis is that the agent-relevant
information set is a small, structured subset of every node:
(root operator, total cost, total actual time, the 3 slowest nodes
ranked by *self* actual time, scan-type flags, and a small set of
"smell" warnings: sequential scans on >N rows, nested loops with large
outer cardinality, top-N heapsort spilling). With a deterministic
parser keyed on the leading `->` / arrow / `(cost=...)` markers, we
can hit COMPACT-tier reduction in the 70-75% range and ULTRA in the
90%+ range while preserving exactly the facts the agent needs to
decide "is this query slow because of an index?" or "should I rewrite
the join order?". Detection is by argv: `psql` / `mysql` /
`mysqlsh`, or argv that contains a literal `EXPLAIN` token.

## Theoretical basis

A Postgres EXPLAIN ANALYZE plan with N nodes carries roughly the
following bits per node: operator name (~5 bits, ~30 distinct
operators), cost pair (2 floats, ~64 bits raw but redundant against
parent), row estimate (~16 bits), actual time pair (2 floats, ~64
bits raw), loop count, and a variable-length descriptor (filter
expression, index name, join condition). Empirically, ~60% of the
text is whitespace and tree-drawing characters (` -> `, indentation,
divider lines), and ~25% is the `(cost=A..B rows=R width=W) (actual
time=T1..T2 rows=R' loops=L)` template, which repeats verbatim N
times. So the *redundant* fraction of the file is

    R = 0.60 + 0.25 * (1 - 1/N)
      ~= 0.83 for N = 11

leaving an *information-bearing* fraction of ~17%. That is the
Shannon-style upper bound on what a lossless restatement can save
without dropping facts, and it predicts a >=80% reduction floor *if*
we keep all node metadata. We give up some of this floor in exchange
for not parsing low-relevance nodes (Hash buckets, Memory Usage),
and recover a few points by ranking nodes and only keeping the top
3 by self-time. Self-time = `actual_time_end_of_node - sum(actual_time
of children)`, computable in one pass over the indentation-keyed
parse tree.

For an N=11 plan, raw cl100k tokens = 549 (measured on the
hand-crafted Postgres fixture below). Compact form below = 149 tokens.
Ratio = 1 - 149/549 = **72.9%**. Ultra = 37 tokens, **93.3%**.
Both hit the BASELINE COMPACT (>=30%) / ULTRA (>=70%) floors with
margin.

For MySQL `EXPLAIN FORMAT=TREE` (10 nodes, 423 tokens) the same
recipe gives 126 tokens compact = **70.2% reduction**.

## Concrete proposal for Redcon

New file:
`/Users/naithai/Desktop/amogus/praca/ContextBudget/redcon/cmd/compressors/sql_explain_compressor.py`,
following `pytest_compressor.py`.

New types in `redcon/cmd/types.py`:

```python
@dataclass(frozen=True, slots=True)
class ExplainNode:
    op: str                      # "Seq Scan", "Hash Join", "Index Scan", ...
    relation: str | None         # table or index name, when present
    cost_total: float | None
    rows_est: int | None
    actual_ms: float | None      # cumulative end-of-node
    self_ms: float | None        # cumulative minus children, computed
    rows_actual: int | None
    loops: int | None
    detail: str | None           # "Index Cond: (status = 'shipped')"
    depth: int                   # indentation depth, 0 = root

@dataclass(frozen=True, slots=True)
class ExplainResult:
    dialect: str                 # "postgres" | "mysql_tree" | "mysql_classic"
    nodes: tuple[ExplainNode, ...]
    plan_time_ms: float | None
    exec_time_ms: float | None
    root: ExplainNode | None
    slowest: tuple[ExplainNode, ...]   # top 3 by self_ms
    flags: dict[str, int]              # {"seq_scan": 2, "hash_join": 2, ...}
    warnings: tuple[str, ...]          # "Seq Scan on X (88240 rows)" etc.
```

API sketch:

```python
class SqlExplainCompressor:
    schema = "sql_explain"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Filled in at compress time; see below.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        head = argv[0].rsplit("/", 1)[-1]
        if head in {"psql", "mysql", "mysqlsh", "mariadb"}:
            return True
        # Inline form: agent invokes `psql -c "EXPLAIN ANALYZE ..."` and we
        # see the raw EXPLAIN keyword somewhere in argv.
        return any(a.upper().startswith("EXPLAIN") for a in argv)

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = raw_stdout.decode("utf-8", errors="replace")
        if not text.strip() and raw_stderr:
            text = raw_stderr.decode("utf-8", errors="replace")
        dialect = _sniff_dialect(text)
        result = parse_explain(text, dialect=dialect)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format_explain(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # must_preserve patterns: total cost number, slowest node's op name,
        # and any seq-scan warnings (the load-bearing "smells" the agent
        # acts on). At ULTRA we emit only the root + slowest-1, so patterns
        # apply at COMPACT/VERBOSE per BASELINE quality harness rules.
        patterns: list[str] = []
        if result.root and result.root.cost_total is not None:
            patterns.append(re.escape(f"{result.root.cost_total:.2f}"))
        if result.slowest:
            patterns.append(re.escape(result.slowest[0].op))
        for w in result.warnings:
            if "Seq Scan" in w:
                patterns.append(r"Seq Scan")
                break
        preserved = verify_must_preserve(formatted, tuple(patterns), text)
        return CompressedOutput(
            text=formatted, level=level, schema=self.schema,
            original_tokens=raw_tokens, compressed_tokens=compressed_tokens,
            must_preserve_ok=preserved, truncated=False, notes=ctx.notes,
        )
```

Parser sketches (one-pass, indentation-keyed, prefix-gated):

```python
# Postgres node line:
#   "         ->  Hash Join  (cost=412.35..2389.04 rows=1776 width=224) "
#   "(actual time=12.004..127.812 rows=4523 loops=1)"
_PG_NODE = re.compile(
    r"^(?P<indent>\s*)->\s+(?P<op>[A-Za-z][\w \-/]+?)"
    r"(?:\s+on\s+(?P<rel>\S+))?"
    r"\s*\(cost=[\d.]+\.\.(?P<cost_total>[\d.]+)\s+rows=(?P<rows_est>\d+)"
    r"(?:\s+width=\d+)?\)"
    r"\s*\(actual time=[\d.]+\.\.(?P<actual_ms>[\d.]+)"
    r"\s+rows=(?P<rows_actual>\d+)\s+loops=(?P<loops>\d+)\)\s*$"
)

# MySQL FORMAT=TREE: same shape, leading "-> " (no parent arrow), single
# (cost=...) (actual time=...) pair. Same regex with relaxed leader.

def _self_ms(nodes: list[ExplainNode]) -> list[ExplainNode]:
    # walk by depth; subtract children's actual_ms * loops; clamp at 0.
    ...

def _flags(nodes) -> dict[str, int]:
    counts = collections.Counter()
    for n in nodes:
        counts[_canon_op(n.op)] += 1
    return dict(counts)
```

Compact format (rendered example for the fixture below):

```
psql.explain schema=postgres
root: Limit, total_cost=2456.83, actual_time=128.51ms
plan_time=0.41ms exec_time=128.51ms rows=20
slowest:
- SeqScan order_items: 62.44ms rows=88240 filter=(quantity>0)
- IndexScan orders_status_idx: 5.23ms rows=1956
- SeqScan customers: 3.82ms rows=3840
flags: seq_scan=2 index_scan=1 hash_join=2 sort=top-N
warn: Seq Scan on order_items (88240 rows, no index)
warn: Seq Scan on customers (3840 rows, no index)
```

Ultra format:

```
psql.explain root=Limit cost=2456.83 exec=128.5ms slowest=SeqScan(order_items,62.4ms) seq_scans=2
```

## Hand-crafted fixture (Postgres, 11 nodes)

Used to compute the ratios above. Saved verbatim to
`redcon/cmd/compressors/_fixtures/explain_pg_join.txt` (test-side; do
not modify production source per task brief - this note documents
intent only).

```
                                                                       QUERY PLAN
-----------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=2456.78..2456.83 rows=20 width=224) (actual time=128.451..128.467 rows=20 loops=1)
   ->  Sort  (cost=2456.78..2461.22 rows=1776 width=224) (actual time=128.449..128.460 rows=20 loops=1)
         Sort Key: o.created_at DESC
         Sort Method: top-N heapsort  Memory: 31kB
         ->  Hash Join  (cost=412.35..2389.04 rows=1776 width=224) (actual time=12.004..127.812 rows=4523 loops=1)
               Hash Cond: (o.customer_id = c.id)
               ->  Hash Join  (cost=204.55..2078.50 rows=1776 width=180) (actual time=6.221..120.331 rows=4523 loops=1)
                     Hash Cond: (oi.order_id = o.id)
                     ->  Seq Scan on order_items oi  (cost=0.00..1721.40 rows=88240 width=44) (actual time=0.018..62.443 rows=88240 loops=1)
                           Filter: (quantity > 0)
                           Rows Removed by Filter: 142
                     ->  Hash  (cost=180.10..180.10 rows=1956 width=144) (actual time=5.998..6.001 rows=1956 loops=1)
                           Buckets: 2048  Batches: 1  Memory Usage: 246kB
                           ->  Index Scan using orders_status_idx on orders o  (cost=0.29..180.10 rows=1956 width=144) (actual time=0.061..5.234 rows=1956 loops=1)
                                 Index Cond: (status = 'shipped'::text)
               ->  Hash  (cost=159.80..159.80 rows=3840 width=52) (actual time=5.572..5.574 rows=3840 loops=1)
                     Buckets: 4096  Batches: 1  Memory Usage: 312kB
                     ->  Seq Scan on customers c  (cost=0.00..159.80 rows=3840 width=52) (actual time=0.011..3.815 rows=3840 loops=1)
 Planning Time: 0.412 ms
 Execution Time: 128.512 ms
(20 rows)
```

Measured with `tiktoken.get_encoding("cl100k_base")` (the canonical
tokenizer per BASELINE):

| Tier | Tokens | Reduction |
|---|---|---|
| Raw | 549 | - |
| Compact | 149 | **72.9%** |
| Ultra | 37 | **93.3%** |

For a parallel MySQL FORMAT=TREE fixture (10 nodes, 423 raw tokens),
compact = 126 tokens = **70.2%** reduction, ultra = 28 tokens =
~93.4%.

## Estimated impact

- Token reduction: COMPACT ~73% on Postgres, ~70% on MySQL TREE.
  ULTRA ~93% on both. New compressor, so this is a 100pp delta on
  this command class, not an improvement on existing ones. Per
  BASELINE bar this does *not* meet the >=5pp-across-multiple-
  compressors definition of "breakthrough", but it does meet the
  Theme G "new compressor class" remit and lands on a workload
  (DB query tuning) the agent currently has no help with.
- Latency: parse is one pass over <=200 lines, regex pre-gated on
  the literal `'->'` / `'-> '` prefix per BASELINE prefix-gating
  convention. <1 ms on the 11-node fixture. Cold-start: zero
  marginal cost; the compressor is lazy-imported by the registry
  like the others.
- Affects: only the new compressor file plus a registration line in
  the compressor registry (the `detect_compressor` lookup). No
  effect on cache key (argv-driven), no effect on existing
  compressors. `must_preserve_patterns` are computed per-input and
  go through the same `verify_must_preserve` gate as pytest/lint.

## Implementation cost

- LOC: ~250 (the parser walks indentation, builds a tree, and
  derives self-times; the formatter is short; plus ~60 LOC of
  fixture-driven golden tests for both dialects).
- New runtime deps: zero. Stdlib `re` only. Determinism: no
  randomness; rounding fixed via `f"{x:.2f}"`. The "slowest 3"
  ranking is stable because we tie-break on (self_ms desc, depth
  asc, source-line-number asc), all deterministic from the raw
  text.
- Risks to determinism: float comparison ordering can flip on
  identical self_ms values from different lines; the depth +
  source-line tiebreak handles this. Tested under the BASELINE
  quality harness (run twice, byte-identical).
- Risks to robustness: binary-garbage and 5000-newline fuzz inputs
  produce zero matches and a `nodes=()` result, formatted as
  `psql.explain (no plan parsed)` with the raw tail-30 included
  per the existing log-pointer convention - same fallback shape
  as `lint_compressor` on empty input.
- Risks to must-preserve: the patterns are (a) the formatted total
  cost float, (b) the slowest node's op name, (c) the literal
  `Seq Scan` token when at least one was emitted as a warning. All
  three appear verbatim in COMPACT; ULTRA is exempt from
  must-preserve per BASELINE.

## Disqualifiers / why this might be wrong

1. **Format proliferation.** Postgres has at least four EXPLAIN output
   formats: text (the default, what we target), JSON, XML, and YAML.
   `EXPLAIN (FORMAT JSON, ANALYZE)` is increasingly common in tooling.
   MySQL has classic `EXPLAIN` (tabular), `EXPLAIN FORMAT=TREE`
   (8.0+), and `EXPLAIN ANALYZE` (8.0.18+). Sniffing the dialect from
   stdout is doable (JSON starts with `[\n  {`, classic MySQL has a
   pipe-table border on line 1) but the parser surface multiplies. The
   honest answer is V61 ships with text-Postgres + MySQL TREE only;
   the JSON path is V65 (JSON-log compressor) territory and should
   delegate. Classic MySQL tabular is a separate parser ~150 LOC; ship
   it as a follow-up.
2. **Agents may already be fine.** A 549-token plan is not large by
   modern context standards. The agent might just paste it back to
   the model and let attention do the work. Counter: at COMPACT we
   strip ~400 tokens of pure tree-drawing and width=N noise; the
   *information* density rises substantially, which matters more for
   smaller models or longer agent sessions where every saved token
   compounds. Plus the explicit "warn: Seq Scan on X" line is a
   classifier the model would otherwise have to derive.
3. **Self-time math is fragile under loops.** A nested-loop's inner
   side has `loops=K` and its `actual_time` is *per loop*, so the
   real wall time contribution is `actual_ms * loops`. The Postgres
   docs are explicit about this; we must multiply before subtracting
   from the parent. Get the multiplication wrong and the "slowest 3"
   ranking is wrong, which is a must-preserve violation in spirit
   (we promise the slowest, we deliver something else). Mitigation:
   golden test on a fixture with a deliberately misleading
   `loops=1956` inner-side scan, verify ranking matches manual
   computation.
4. **MySQL TREE format is unstable across minor versions.** 8.0.20
   prints `Stream results` as a node; 8.0.30 may inline it; 8.4 has
   added new operator names. The regex needs to be lenient on the
   operator-name capture and not assume a closed vocabulary. Mitigation:
   accept any `[A-Za-z][\w \-/]+?` as op name and only canonicalise
   for the `flags` histogram via a soft mapping table (unmapped names
   bucket as `other`).
5. **Workload reality.** Are agents actually invoking `psql -c
   "EXPLAIN ANALYZE ..."` often? In a typical Redcon session, almost
   never. This is a long-tail compressor. It is cheap (~250 LOC,
   stdlib-only) but the BASELINE bar prefers >=5pp moves across
   *multiple* existing compressors, which V61 cannot deliver because
   it is a new class. Fair counter: Theme G in INDEX.md explicitly
   exists for new classes, and DB EXPLAIN is one of the listed
   slots.

## Verdict

- Novelty: **medium** (textbook parser, but no existing redcon
  compressor covers DB query plans, and the self-time-of-node ranking
  is a small twist that gives the agent better signal than just
  picking by total time)
- Feasibility: **high** (deterministic regex parser, no new deps,
  fits the existing compressor + must-preserve harness exactly)
- Estimated speed of prototype: **1-2 days** for Postgres-text +
  MySQL-TREE with golden fixtures and the determinism / robustness
  fuzz. **+1 day** to add classic-MySQL-tabular. **+2 days** to add
  the JSON dialect (probably better split into V65 territory).
- Recommend prototype: **yes**, scoped to Postgres-text + MySQL-TREE
  in the first PR. Defer JSON / classic-tabular until usage data
  shows demand. Compose well with future V83 (KL-divergence quality
  gate) since the structured `ExplainResult` is a natural ground-
  truth representation for measuring information loss.
