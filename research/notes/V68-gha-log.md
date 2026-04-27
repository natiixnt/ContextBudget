# V68: CI annotations / GitHub Actions log compressor

## Hypothesis

`gh run view --log` (and the parallel `--log-failed`) flattens an entire
GitHub Actions run into a single newline stream where every line is
prefixed with `<job-name>\t<step-name>\t<RFC3339-timestamp>\t<payload>`
and where the payload itself can carry semantic markers
(`::group::`, `::endgroup::`, `::error file=...,line=...::`,
`::warning ::`, `::notice ::`, `::add-mask::`, `##[group]`, etc.).
On a green run the agent does not need any of it; on a failed run the
agent needs (a) which job and which step failed, (b) the last ~30 lines
of payload before the failing line, and (c) the deduplicated list of
annotations the runner emitted. Everything else (matrix duplicates,
`Set up actions`, `Post Run` housekeeping, the ~30 lines of
`apt-get install` per setup-* action, ANSI colour codes, timestamps that
all share the same date) is overhead. The claim is that a dedicated
compressor can hit **>=95% reduction** on a real failed-run log and
**>=98%** on a clean run, beating both the generic `log_pointer`
fallback and the lint compressor that occasionally swallows
`path:line:col:` lines from a CI step. The interesting subclaim is that
the workflow command syntax itself
(<https://docs.github.com/en/actions/learn-github-actions/workflow-commands-for-github-actions>)
is a stable, BNF-shaped sub-language that lets us discard 99% of the
stream and still emit a complete, lossless annotation manifest at COMPACT.

## Theoretical basis

Three independent reductions multiply.

1. **Per-line prefix is a constant header.** Every line carries
   `<job>\t<step>\t<ISO-ts>\t`. Letting J be the average job-name length,
   S the step-name length, the prefix is `J + S + 24` bytes per line
   (24 = `2025-04-26T12:34:56.7890123Z\t` minus a few). On a 100k-line
   log with J=20, S=30 that is 100000 * 74 = 7.4 MB of pure prefix.
   Stripping prefix from the body and emitting `(job, step)` once per
   contiguous run reduces this term to O(jobs * steps) = O(few hundred)
   bytes total. Compression ratio on prefix alone:
   ratio_prefix = 1 - (J_avg + S_avg + 24) / L_avg_line ~ 1 - 74/110 ~
   33% before any payload-side work.

2. **Annotation entropy is low.** GitHub workflow commands form a
   regular language: `::error\s+(file=\S+,)?(line=\d+,)?(col=\d+,)?(title=...,)?::message`.
   Real runs rarely emit more than O(10) distinct annotations across
   100k lines (typical: 1 failing test x 5 matrix permutations = 5
   annotations, or 1 lint rule fired across 12 files = 12 annotations).
   By Shannon, the total payload entropy of the annotation set is
   bounded by H(distinct_annotations) <= log2(few hundred) bits, ~ a
   handful of bytes per annotation. The current `log_pointer` tier
   ignores this structure entirely and just spills.

3. **Failed-step locality.** Empirically (see `actions/runner` source,
   `runner/src/Runner.Worker/StepsRunner.cs`), the failing step is the
   last step whose conclusion is `failure`. The agent's question "what
   broke and why" is answered by the last K lines of *that step's*
   payload (K = 30 is the harness convention used by the existing
   `log_pointer` tail). All other steps' bodies are noise once we know
   the step boundaries from `##[group]Run <step>` markers and the
   `Run <step-name>` lines `gh run view --log` emits. So the failed-step
   extract has size at most K * L_payload_avg = 30 * 60 = 1800 bytes
   regardless of how big the run was. Compression ratio for a 100k-line
   / 7 MB log carrying a single failed step:
   ratio_fail = 1 - (1800 + 200 * J_count + 50 * annot_count) / 7e6 ~
   1 - 0.0003 ~ **99.97%**.

   For a green run the failed-step term is 0 and we emit only the job
   summary table:
   ratio_green = 1 - (200 * J_count) / 7e6 ~ 1 - 4e-5 ~ **99.996%**.

Lower bound (the only way this loses): a log that is 100% annotations
already (e.g. a custom action that prints `::warning::` for every line
of a 100k-line build). Then by Shannon we cannot compress below the
entropy of the annotation set itself; if all 100k annotations are
distinct, COMPACT degrades to "first/last 30 + count + dedup-by-prefix"
and we land on ~80% reduction, which is still strictly better than the
log_pointer tier (~95% but with the failed-step extract missing).

## Concrete proposal for Redcon

New file `redcon/cmd/compressors/gha_log_compressor.py` (~250 LOC),
plus a `GhaLogResult` / `GhaJobSummary` / `GhaAnnotation` dataclass
trio added to `redcon/cmd/types.py`. No changes to pipeline; the
compressor self-registers via the existing `matches(argv)` protocol.

### Schema

```python
@dataclass(frozen=True, slots=True)
class GhaAnnotation:
    level: str           # "error" | "warning" | "notice"
    file: str | None
    line: int | None
    col: int | None
    title: str | None
    message: str

@dataclass(frozen=True, slots=True)
class GhaJobSummary:
    job: str
    conclusion: str          # "success" | "failure" | "cancelled" | "skipped"
    failed_step: str | None
    duration_s: float | None
    step_count: int

@dataclass(frozen=True, slots=True)
class GhaLogResult:
    jobs: tuple[GhaJobSummary, ...]
    annotations: tuple[GhaAnnotation, ...]
    failed_tail: str          # last 30 lines of failing step payload, no prefixes
    raw_lines: int
    raw_bytes: int
```

### Detection (per the prompt)

```python
def matches(self, argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    # gh run view --log / --log-failed
    if argv[0] == "gh" and len(argv) >= 3 and argv[1] == "run" and argv[2] == "view":
        return True
    # gh actions ...
    if argv[0] == "gh" and "actions" in argv[:3]:
        return True
    # Sniff stdin / file argv when piped: caller passes raw_stdout into
    # the compressor anyway, so we expose a content-sniff fallback in
    # detect_compressor (BASELINE invariant: matches() is argv-only;
    # add a sibling `content_sniff(prefix: bytes) -> bool` returning
    # True when first 4 KiB contains both b"::group::" and b"::error::"
    # OR b"##[group]" and a `\t<RFC3339>\t` pattern).
    return False
```

The content-sniff hook is a small new addition to the `Compressor`
Protocol (optional method, default-False) and the dispatcher
(`detect_compressor`) tries argv first, then content-sniff. This does
not break determinism (same bytes -> same answer) and is a strict
superset of the current dispatch.

### Parser (sketch)

```python
_PREFIX = re.compile(rb"^(?P<job>[^\t]+)\t(?P<step>[^\t]+)\t(?P<ts>\S+)\t(?P<body>.*)$")
_GROUP_OPEN  = re.compile(rb"^(?:::group::|##\[group\])(?P<name>.*)$")
_GROUP_CLOSE = re.compile(rb"^(?:::endgroup::|##\[endgroup\])$")
_ANNOT       = re.compile(
    rb"^::(?P<lvl>error|warning|notice)"
    rb"(?:\s+(?P<kvs>[^:]*?))?::(?P<msg>.*)$"
)
_KV          = re.compile(rb"(file|line|col|title|endLine|endColumn)=([^,]+)")
_ANSI        = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
_RUN_STEP    = re.compile(rb"^Run\s+(?P<step>.+)$")  # appears at step start

def parse_gha_log(raw: bytes) -> GhaLogResult:
    jobs: dict[str, _JobAccum] = {}
    annots: list[GhaAnnotation] = []
    last_failed: tuple[str, str] | None = None       # (job, step)
    payload_ring: dict[tuple[str, str], collections.deque[bytes]] = {}
    # 30-line ring per (job,step) so we can publish failed_tail without
    # holding the whole payload in memory.
    raw_lines = 0
    for line in raw.splitlines():
        raw_lines += 1
        m = _PREFIX.match(line)
        if not m:
            continue
        job, step, ts, body = m["job"], m["step"], m["ts"], _ANSI.sub(b"", m["body"])
        key = (job, step)
        ring = payload_ring.setdefault(key, collections.deque(maxlen=30))
        ring.append(body)
        a = _ANNOT.match(body)
        if a:
            annots.append(_build_annot(a))
            if a["lvl"] == b"error":
                last_failed = key
                # we still let the conclusion line below confirm it
        if body.startswith(b"##[error]") or body == b"Process completed with exit code 1.":
            last_failed = key
        # job/step bookkeeping in `jobs` accum (start ts, end ts, conclusion)
    # publish
    failed_tail = b""
    if last_failed:
        failed_tail = b"\n".join(payload_ring[last_failed])
    annots = _dedup_annotations(annots)        # exact-tuple dedup, stable order
    return GhaLogResult(...)
```

Notes:

- ANSI strip is a single regex applied once per body byte-string;
  reuses the rationale from V38 (already shipped).
- `_dedup_annotations` collapses the matrix-fanout case: an annotation
  fired by 5 jobs with identical `(level,file,line,msg)` becomes one
  entry with `seen_in: tuple[str, ...]` of job names. That alone takes
  a 5x duplication down to 1x.
- 30-line rings cap memory at `O(jobs * steps * 30 * L_avg)` regardless
  of total raw size. For a fanned-out matrix with 50 (job,step) pairs
  that is 50 * 30 * 100 = 150 KB of buffer. Cheap.

### Format levels

```python
def _format_ultra(r: GhaLogResult) -> str:
    failed = [j for j in r.jobs if j.conclusion == "failure"]
    if not failed:
        return f"gha: {len(r.jobs)} jobs ok"
    return (
        f"gha: {len(failed)}/{len(r.jobs)} jobs failed; "
        f"{sum(1 for a in r.annotations if a.level == 'error')} errors, "
        f"{sum(1 for a in r.annotations if a.level == 'warning')} warnings"
    )

def _format_compact(r: GhaLogResult) -> str:
    out = [_format_ultra(r), ""]
    out.append("jobs:")
    for j in r.jobs:
        marker = "FAIL" if j.conclusion == "failure" else j.conclusion[:4]
        s = f"  {marker} {j.job}"
        if j.failed_step:
            s += f"  step={j.failed_step}"
        if j.duration_s is not None:
            s += f"  {j.duration_s:.0f}s"
        out.append(s)
    if r.annotations:
        out.append("")
        out.append("annotations:")
        for a in r.annotations:
            loc = f"{a.file}:{a.line}" if a.file else "-"
            ttl = f" [{a.title}]" if a.title else ""
            out.append(f"  {a.level} {loc}{ttl}: {a.message}")
    if r.failed_tail:
        out.append("")
        out.append("--- failed step tail ---")
        out.append(r.failed_tail.decode("utf-8", errors="replace"))
    return "\n".join(out)
```

VERBOSE keeps full payload of the failing step (no 30-line cap) plus
group structure of all steps. ULTRA emits the headline only.

### must_preserve_patterns

```python
@property
def must_preserve_patterns(self) -> tuple[str, ...]:
    return (
        r"^gha:",                       # header
        r"FAIL",                        # at least one FAIL marker if failure
        # plus dynamic patterns built per-call from observed failing-step
        # names and from observed annotation file paths, the same trick
        # `LintCompressor.compress` uses (top-30 paths -> escaped patterns).
    )
```

### Estimated impact

- Token reduction: **~99%** on a typical failed-run log
  (100k lines / ~7 MB raw -> ~600-1500 tokens compressed depending on
  annotation fanout). On a clean run, **~99.5%**: header + N job lines.
  Worst case (all-annotation log with no duplicate prefixes) ~**80%**.
  Clears the BASELINE 70% ULTRA / 30% COMPACT floors with three orders
  of magnitude of headroom.
- Latency: cold-start unchanged (lazy-import per existing pattern).
  Warm-parse: dominated by single-pass regex on the body; on a 7 MB log
  with prefix-gating (test first byte for `:` or `#` before running the
  annotation regex) we are looking at ~50-100 ms. The previous path for
  the same input is the `log_pointer` spill, which is essentially a
  `write()` plus `splitlines()[-30:]` -> ~10-20 ms. So we trade ~50 ms
  warm-parse for **two extra orders of magnitude** of reduction and a
  structured annotation list. Net win.
- Affects: `redcon/cmd/compressors/__init__.py` (export), pipeline
  `detect_compressor` (insert before `log_pointer` so size threshold
  doesn't preempt; pipeline currently spills above 1 MiB *before* a
  compressor runs - V68 needs an opt-out so that argv `gh run view`
  bypasses the size gate, since the whole point of this compressor is
  to handle exactly the inputs that would otherwise spill). Quality
  harness fixtures: 3 new files under `redcon/symbols/` or
  `tests/fixtures/cmd/gha/` (one green, one single-failure, one
  matrix-fanout failure). Cache layer: unaffected (argv-only key).

## Implementation cost

- **~280 LOC**: 200 in `gha_log_compressor.py`, 40 in
  `types.py`, 20 in `pipeline.py` (size-gate bypass for known
  big-but-structured argv), 20 in `__init__.py` and quality registry.
- **Runtime deps: zero new.** stdlib `re`, `collections.deque`,
  `dataclasses`. No `gh` dep at runtime; we parse the bytes, we do not
  invoke the CLI. (User invokes `gh run view --log` themselves; we
  see only raw_stdout.)
- **Risks to determinism:** the annotation dedup must use a stable
  tie-break (sort by `(level, file or '', line or 0, message)` after
  exact-key dedup). With that, output is byte-identical for byte-
  identical input. Pass.
- **Risks to robustness:** the harness's "binary garbage" / "5000
  newlines" / "random word spam" inputs all hit the prefix regex and
  fail to match -> empty result -> header `gha: 0 jobs ok` (or
  similar). Acceptable. The truncated-mid-stream case is handled by
  the deque-based payload ring; an unfinished group leaves
  `last_failed=None` and we emit no `failed_tail`, which is the correct
  conservative behaviour.
- **Risks to must_preserve:** the dynamic pattern set (escape annotation
  file paths) follows the established `LintCompressor` pattern; same
  precedent, same risk profile. ULTRA is exempt from the gate per
  BASELINE.

## Disqualifiers / why this might be wrong

1. **`log_pointer` already exists and may be good enough.** For a 7 MB
   log the pipeline already spills to `.redcon/cmd_runs/<digest>.log`
   and emits "tail-30 + path". The agent can re-grep on demand. The
   counter: that path emits zero structure (no job table, no annotation
   list, no per-step boundary) and the tail-30 is the *very last 30
   lines of the run*, which on GHA is invariably "Cleaning up
   orphan processes; Post-Run actions/checkout@v4; Job complete:
   failure" - i.e. content-free. V68 makes the tail meaningful by
   anchoring it to the failing step, not the byte-EOF.
2. **`gh run view --log-failed` exists.** GitHub's own CLI can already
   filter to the failed job's log. The counter: it still emits the
   full prefixed body of the failed job (often tens of thousands of
   lines for a slow build), and it still bears all the per-line prefix
   overhead V68 strips. `--log-failed` is an upstream filter, not a
   compressor; V68 sits *on top* of either flag.
3. **GitHub may change the line format.** The `<job>\t<step>\t<ts>\t`
   format is stable for years but is not a documented public contract
   (it is what `gh run view --log` emits, which is the runner-side
   `_diag` log). Mitigation: if `_PREFIX` fails to match >50% of lines,
   fall back to "treat each line as raw payload, no per-step grouping",
   which still wins on annotation extraction alone.
4. **Annotation regex may collide with action output.** A user action
   that legitimately prints `::error something::` outside of CI command
   intent would be eaten. Counter: the GHA workflow command syntax is
   precisely defined; any string starting `::level::` *is* an annotation
   by spec. There is no false positive, only by-design semantics.
5. **Detection from stdin sniff is a Protocol change.** Adding
   `content_sniff(prefix)` to the `Compressor` Protocol is the only
   non-trivial structural change. If maintainers prefer to keep
   `matches()` argv-only, V68 still ships - it just won't activate when
   the user pipes a saved log file via `cat run.log | redcon run --
   ...`, which is a marginal use case.
6. **Multi-run / re-run logs concatenate.** A re-run of a failed job
   prepends a new section. The dedup by `(level,file,line,message)`
   collapses repeated annotations, so the agent sees one entry; the
   per-job summary correctly shows the latest conclusion if we sort by
   the highest `step_count` per job-name. Risk: if naming is identical
   across runs we may not distinguish original-fail from re-run-pass
   without parsing the leading run-id, which is *not* in the log body.
   Acceptable degradation.

## Verdict

- Novelty: **medium**. Same shape as the existing lint / docker /
  kubectl compressors; the wins are real but architecturally
  incremental. The `content_sniff` Protocol extension is a small
  generalisation that other future compressors (V65 JSON-log, V66 HTTP
  access log) will also want, so the cost amortises.
- Feasibility: **high**. All-stdlib, pure-text, single-pass, fixed
  memory.
- Estimated speed of prototype: **~2 days** including 3 fixtures
  (green / single-fail / matrix-fanout), quality-harness wiring,
  and the size-gate bypass in `pipeline.py`.
- Recommend prototype: **yes**. Single-vector ROI is high (a
  ~99% compressor on one of the most-painful agent inputs - a 100k-line
  CI log) and it composes cleanly with the existing pipeline. The
  one architectural ask (`content_sniff` hook) is small and is a
  prerequisite that several Theme G vectors will share.
