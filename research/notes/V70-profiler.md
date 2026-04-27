# V70: Profiler output compressor (flamegraph DAG -> top-K paths)

## Hypothesis

A collapsed-stack profile (the `frame1;frame2;...;frameN <count>` line
format produced by py-spy `--format raw`, perf `stackcollapse-perf.pl`,
and most other Brendan-Gregg-style tools) is one of the worst input
shapes for an LLM context. For a typical 30-second py-spy run the file
is 3000-10000 lines and 200-800 KiB; almost none of it is decision-
relevant. What an agent actually needs from a profile is small and
structured: the top-K hot stacks ranked by total samples (with the % of
total each represents), an aggregate self-time per leaf frame, and the
shared-prefix DAG so it can see "all 60% of cost goes through
`requests.Session.send -> urllib3...connect`". The hypothesis is that a
purpose-built compressor exploiting (a) the prefix-DAG structure of
collapsed stacks and (b) the heavy-tailed sample distribution can drive
COMPACT-tier reduction to roughly **92-96%** on real py-spy / perf
output - higher than diff (97%) is unrealistic because the value here
is the rank-ordered top-K, not just a count, but it should land in the
same league as the test-runner compressors and well above lint (which
keeps per-issue detail). The argv detection surface is small and
unambiguous: `py-spy record/dump`, `perf record`, `perf script`, and a
stdin sniff for `;`-separated tokens followed by an integer.

## Theoretical basis

A collapsed-stack profile is a multiset of paths in a tree (the call
DAG with caller-as-root). Let the file contain `n` distinct stacks
S_1..S_n with sample counts c_1..c_n, total `N = sum(c_i)`. Let `D` be
the set of distinct frames (the alphabet) of size `|D|`. In the raw
representation the cost in characters is

    R_raw = sum_i (sum_{f in S_i} (|f| + 1)) + delta(c_i)

where the `+1` is the `;` (or trailing space-then-count) separator.
For Python profiles, `|f|` averages 30-60 chars (`module.Class.method
(file.py:123)`), and stacks are typically 20-80 frames deep; so each
line costs roughly 600-3500 chars.

Two structural facts let us crush this.

**Fact 1: heavy-tailed sample distribution.** Empirically (Gregg 2016,
Flame Graph paper; replicated on every py-spy fixture I have seen) the
sample counts follow approximately a Zipf or stretched-exponential
distribution. For Zipf with exponent `s ~= 1.0`, the top K stacks
account for a fraction roughly

    F(K) = H_K / H_n  where H_k = sum_{j=1..k} 1/j

For n=5000, F(20) ~= H_20 / H_5000 = 3.598 / 9.094 ~= 0.396, i.e.
the top-20 stacks alone cover ~40% of all samples. For the more
realistic stretched-exponential `c_i ~= C * exp(-(i/lambda)^beta)` with
`beta ~= 0.5, lambda ~= 50` typical of py-spy on a web server, the
top-20 covers ~70% and the top-100 covers ~95%. So a top-K=20 summary
plus a "remaining N-20 stacks account for X%" tail is information-
preserving for the agent's purpose (find the hot path).

**Fact 2: prefix-DAG redundancy.** The n stacks share a small number of
prefixes. If we build the trie T over S_1..S_n, the number of distinct
nodes |T| is bounded by the number of distinct (caller, callee) edges,
which for a real program is `O(|D|^2)` at worst but in practice is
~3*|D|. Encoding the same stacks as edges of T plus per-leaf counts
costs

    R_dag ~= |T| * avg(|f|+1) + n * delta(c_i)

For a profile with |D|=400 distinct frames, |T| ~= 1200 edges, n=5000
stacks: R_dag is roughly 1200*40 = 48 KB versus R_raw's
5000*1500 = 7.5 MB - a 150x structural reduction before we even pick
top-K. We are not going to deliver 150x because we will not emit the
full DAG; we will emit only the top-K root-to-leaf paths plus a leaf
self-time histogram, and that fits in ~2 KB.

**Combined back-of-envelope**, for a realistic 5000-line py-spy raw
output of ~600 KiB (~150k cl100k tokens):

    output = top-20 paths (each ~3 frames after collapsing identical
             prefix prefixes plus a count and percentage)
           + top-10 leaf frames by self-samples
           + 1-line tail "K more stacks (M%)"
    output_size ~= 20 * (60 chars/path) + 10 * 50 + 60
                ~= 1200 + 500 + 60 = 1760 chars
                ~= 440 cl100k tokens

Reduction = 1 - 440/150000 = **99.7%** in the best case, dragging back
to a more honest **92-95%** once we keep enough frame detail to be
useful (we do not want to collapse to one-line summaries that tell the
agent nothing it could not compute by sampling). I am budgeting **94%
COMPACT, 99% ULTRA** as the headline numbers.

## Concrete proposal for Redcon

New file `redcon/cmd/compressors/profiler_compressor.py`. New schema
type `ProfileResult` in `redcon/cmd/types.py`. New registry entry in
`redcon/cmd/registry.py`. No changes to existing compressors.

API sketch:

```python
# redcon/cmd/types.py
@dataclass(frozen=True, slots=True)
class HotPath:
    frames: tuple[str, ...]    # outermost -> innermost
    samples: int
    fraction: float            # samples / total

@dataclass(frozen=True, slots=True)
class ProfileResult:
    tool: str                  # 'py-spy' | 'perf' | 'flamegraph'
    total_samples: int
    distinct_stacks: int
    hot_paths: tuple[HotPath, ...]      # top-K, K=20 default
    self_time: tuple[tuple[str, int], ...]  # top-10 leaf frames by self count
    truncated_tail_samples: int         # N - sum(top-K samples)
```

```python
# redcon/cmd/compressors/profiler_compressor.py (sketch)
class ProfileCompressor:
    schema = "profile"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()  # patterns generated dynamically: top-3 leaf names

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] == "py-spy" and len(argv) >= 2 and argv[1] in {"record", "dump", "top"}:
            return True
        if argv[0] == "perf" and len(argv) >= 2 and argv[1] in {"record", "script", "report"}:
            return True
        return False  # stdin sniff handled in pipeline (see below)

    def compress(self, raw_stdout, raw_stderr, ctx):
        text = raw_stdout.decode("utf-8", "replace") or raw_stderr.decode("utf-8", "replace")
        result = parse_collapsed(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        ...

def parse_collapsed(text: str, top_k: int = 20) -> ProfileResult:
    # one pass: split on ';' until last whitespace-separated integer
    counts: dict[tuple[str, ...], int] = {}
    leaf_self: dict[str, int] = {}
    total = 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or " " not in line:
            continue
        path_str, _, num = line.rpartition(" ")
        if not num.isdigit():
            continue
        c = int(num)
        frames = tuple(path_str.split(";"))
        if not frames:
            continue
        counts[frames] = counts.get(frames, 0) + c
        leaf_self[frames[-1]] = leaf_self.get(frames[-1], 0) + c
        total += c
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    hot = tuple(HotPath(p, c, c/total) for p, c in ranked)
    leaves = tuple(sorted(leaf_self.items(), key=lambda kv: -kv[1])[:10])
    consumed = sum(c for _, c in ranked)
    return ProfileResult(
        tool=_detect_tool_from_text(text),
        total_samples=total,
        distinct_stacks=len(counts),
        hot_paths=hot,
        self_time=leaves,
        truncated_tail_samples=total - consumed,
    )
```

COMPACT formatter prints each hot path as a single line with shared-
prefix elision against the previous emitted path:

    profile py-spy: 87432 samples, 4127 distinct stacks
    [38.2%]  myapp.api.handle_request -> sqlalchemy.engine.execute -> ...
    [11.4%]   ...   -> psycopg2.extensions.fetchall
    [ 9.7%]  myapp.api.handle_request -> json.dumps -> _encode_dict
    ...
    self time: handle_request 41021, fetchall 18840, _encode_dict 9112, ...
    + 4107 more stacks (15.2%)

Stdin sniff for unknown argv (e.g. `cat profile.txt | redcon run -`):
add a 256-byte heuristic in `pipeline.py::detect_compressor` fallback -
"if first non-empty line matches `^[^\s][^;]*(;[^;]+)+\s+\d+$`, dispatch
to ProfileCompressor". Heuristic is cheap and unambiguous; the
`;`-then-trailing-integer pattern does not collide with any of the
other 11 compressors.

Registry entry parallels `_is_lint`:

```python
def _is_profile(argv):
    if not argv:
        return False
    if argv[0] == "py-spy":
        return len(argv) >= 2 and argv[1] in {"record", "dump", "top"}
    if argv[0] == "perf":
        return len(argv) >= 2 and argv[1] in {"record", "script", "report"}
    return False
```

## Estimated impact

- Token reduction: **94% COMPACT, ~99% ULTRA** on a realistic 5000-line
  py-spy fixture (150k -> 8-10k tokens compact, 150k -> 200 tokens
  ultra). On a perf-script-collapsed kernel profile (20k lines, ~1.2 MB,
  ~310k tokens) compact lands ~96% because perf stacks are deeper and
  share more prefix. These numbers put the new compressor between
  pytest (73.8%) and git diff (97.0%), most comparable to find (81.3%)
  since both exploit prefix-DAG structure.
- Latency: cold-start unchanged (lazy-imported via registry, same
  pattern as the other 11 compressors). Warm parse is one linear pass
  over `text.splitlines()` plus a `dict` insert per line; for 5000
  lines that is ~3 ms on a modern CPU, well below the subprocess cost
  of py-spy itself (which is in the seconds).
- Affects: only new files, plus a 4-line registry append and a single
  conditional in `pipeline.py` for the stdin-sniff fallback. No
  existing compressor changes. No cache key change. No change to
  `_meta.redcon` schema other than a new `schema` value `"profile"`.
- Composes with: V64 (stack-trace dedup) - they share the
  frame-template-extraction step; if V64 lands first, ProfileCompressor
  reuses its frame canonicaliser. V53 (t-digest) - we could swap the
  exact `self_time` histogram for a t-digest at ULTRA tier, but the top
  10 leaves are already cheap, so the win is small. V47 (snapshot
  delta) - delta-vs-prior-profile would let an agent see "regression of
  cost on `handle_request` since last commit", a clear future
  extension.

## Implementation cost

- Lines of code: **~180** (compressor ~120, types/registry/glue ~30,
  golden fixture and test ~30). Comparable to `lint_compressor.py`
  (263 LOC) and `kubectl_compressor.py`.
- New runtime deps: **none**. Pure stdlib (`re`, `dataclasses`, no
  parsing libraries). Does not touch network, embeddings, or
  randomness.
- Risks to determinism: zero - sort uses `(-count, frames)` tuple as
  total order, ties broken lexicographically; no `set` order leakage
  because everything goes through `sorted(...)` before formatting.
- Risks to robustness fuzz: low. Binary garbage produces zero
  recognised lines and falls through to "0 samples, 0 stacks", which
  is a valid empty `ProfileResult`. Truncated mid-line is benign
  because the parser skips lines without a trailing integer. The 5000-
  newlines fuzz produces 0 stacks. Random word spam likewise drops
  through. The one mildly concerning case is a *valid-looking* line
  with an enormous count integer (`...; 99999999999999999`) - bounded
  by Python's arbitrary-precision int, so no overflow, but the
  per-line count could legitimately dominate the histogram. We cap
  individual line contributions at `2**40` defensively (a 30-second
  profile cannot have 1e12 samples).
- Risks to must_preserve guarantees: `must_preserve_patterns` is built
  dynamically from the top-3 leaf frame names, mirroring lint's
  approach. They survive into formatted COMPACT output by
  construction (the `self_time` line emits them).
- New attack surface: a malicious profile crafted with 1e7 distinct
  stacks could blow memory in the `counts` dict. Mitigate by reusing
  the existing log-pointer tier (BASELINE: raw output > 1 MiB spills
  to `.redcon/cmd_runs/<digest>.log` and emits a tail-30 pointer). For
  inputs *under* 1 MiB the dict is bounded at ~5000 entries, which is
  fine.

## Disqualifiers / why this might be wrong

1. **Profilers may already collapse before write.** py-spy's
   `--format speedscope` and `--format flamegraph` run their own
   collapse server-side, so the file the agent receives may already be
   summarised. Counter: those formats are still huge (speedscope JSON
   typically 2-10 MiB even after collapse), and the dominant deployment
   inside agentic workflows is the raw `;`-separated form
   (`py-spy dump --pid X` and `py-spy record --format raw`). The
   compressor still wins on those.
2. **Top-K paths can hide the actual problem.** If a regression is in
   stack #47 by sample count but is the only one that changed since
   last run, top-20 hides it. Counter: that is exactly the V47
   snapshot-delta vector's job, and ProfileCompressor exposes
   `distinct_stacks` plus the tail count so the agent knows there is a
   tail; if it cares about delta it asks for VERBOSE or pivots to the
   delta tool.
3. **Frame strings are themselves token-bloated.** A C++/Rust mangled
   symbol with templates `std::__1::shared_ptr<foo::bar::Baz<int, int,
   foo::quux>>::operator->()` can be 200 tokens by itself. We are not
   compressing inside frames. If a real perf profile is dominated by
   such strings the 94% target slides to maybe 85%. Mitigation:
   demangle-and-truncate via `c++filt -t` is out of scope (would need
   a system binary), but a stdlib regex strip of `std::__1::` and
   `< ... >` template parameters in the formatter can shave 30%
   off long frames at near-zero cost. I would not ship that in v1.
4. **Already covered by py-spy itself in `top` mode.** `py-spy top`
   prints a refreshed top-N table directly. If the agent invoked
   `py-spy top` with `--duration N`, the output is already small.
   Counter: that mode is interactive and almost never used in
   non-interactive agent runs; `record` / `dump` are the realistic
   commands and emit raw stacks.
5. **Could be shadowed by V64 (stack-trace dedup).** V64 is already in
   the index for general stack traces (Java exceptions, Python
   tracebacks). If V64 ships a generic dedup-and-template engine it
   could subsume V70. Counter: V64 deduplicates *exception* stacks,
   which carry per-frame source line numbers and exception messages
   and *no count*. V70 deduplicates *sample-counted* stacks, which is
   a different aggregate (sum the counts, rank by total). The two
   datatypes do not cleanly share a formatter; they share a parser at
   most. Treat V70 as a sibling of V64, not a child.

## Verdict

- Novelty: **medium**. The technique (top-K + prefix DAG of a
  collapsed flamegraph) is well-known in profiler tooling
  (Gregg 2016; pprof's `top`, `peek`, and `tree` views; speedscope's
  Sandwich view); applying it inside Redcon's deterministic-compressor
  framework with the cl100k token-aware formatter is the original
  contribution. Not a breakthrough by BASELINE's bar (single
  compressor, ~94% reduction, doesn't move multiple compressors by
  >=5pp), but a clean addition to the open frontier under Theme G
  (new compressor classes).
- Feasibility: **high**. The collapsed-stack format is unambiguous,
  the parser is one regex-free pass, and there is no
  network/embedding/randomness involvement.
- Estimated speed of prototype: **1 day** for a working compressor,
  golden fixture, quality-harness wiring, and registry entry.
  **2 days** including a real py-spy fixture captured against a
  toy Flask/SQLAlchemy app and tuning of the top-K and self-time-
  histogram limits against COMPACT-tier reduction-floor (>=30%) and
  ULTRA-tier (>=70%).
- Recommend prototype: **yes**. Profiles are a natural addition to
  the command-side compressor set, the frame-DAG structure is
  exploitable with stdlib code, and the >90% target compares
  favourably to the existing lineup.
