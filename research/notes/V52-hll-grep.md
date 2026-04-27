# V52: HyperLogLog++ for distinct counts in grep results

## Hypothesis

A grep / ripgrep run can emit thousands of matches whose actual *distinct
content* set is one or two orders of magnitude smaller. The current
`grep_compressor.py` reports `match_count` and `file_count` but never the
number of distinct match-text values, even though that single integer
("5000 matches across 50 files, 1000 distinct") is what tells the agent
whether the result is a small set of repeated hits or a long tail of
unique lines. The proposal is to add a *distinct-text count* to the
schema. The interesting subclaim is that under the current eager-buffer
parser a `set[str]` is already the right tool, but if Redcon ever moves
to a streaming/bounded-memory parse (V58 adaptive sampling, V56 early
kill, V59 PIPE backpressure), an exact set is no longer affordable and
HyperLogLog++ becomes the only deterministic O(1)-memory way to keep the
distinct count. So V52's value is conditional on V58/V56/V59 landing,
and on its own it should ship as a plain `frozenset` count. Calling that
out is the actual contribution.

## Theoretical basis

HyperLogLog (Flajolet, Fusy, Gandouet, Meunier, AofA 2007) and Google's
HyperLogLog++ refinement (Heule, Nunkesser, Hall, EDBT 2013) estimate
the cardinality |D| of a multiset stream from a fixed-size register
array of m buckets. With m = 2^p buckets of 6 bits each (HLL++ uses 6
because we only store a leading-zero count over a 64-bit hash), memory
is

    M = m * 6 bits = 2^p * 6 bits.

For p = 14 (m = 16384) that is 12 KiB and the standard error is

    sigma_rel ~= 1.04 / sqrt(m) = 1.04 / 128 = 0.81%.

For Redcon's typical "how many distinct matches" question, even p = 10
(m = 1024, 768 bytes, sigma_rel ~= 3.25%) is plenty, because the agent
only needs order-of-magnitude.

Compare against the exact alternative. A `set[str]` over k distinct
match-texts of average length L bytes costs at least

    M_set >= k * (56 + L)   bytes

(56 ~= PyObject + hash slot overhead; conservative). For k = 1000,
L = 80 that is ~136 KB, which is fine. For k = 1e6, L = 200 that is
~256 MB, which is not. HLL is asymptotically constant in k.

So the theoretical case for HLL is **strictly streaming**: HLL beats
`set` only when (a) the input is too big to buffer, and (b) we still
want a numeric distinct count. Neither is the case for the existing
eager `parse_grep` / `parse_grep_json`.

Determinism. HLL is randomised in textbook form (chooses a hash). It
becomes deterministic if we fix the hash (e.g. `xxhash64` with a
constant seed, or `siphash` with the all-zero key, or even the bottom
64 bits of `hashlib.blake2b(..., digest_size=8).digest()` for the
no-extra-deps path). Per BASELINE constraint #1 we MUST use a fixed
seed. With a fixed seed the estimator output is a pure function of the
input multiset, satisfying same-input-same-output.

## Concrete proposal for Redcon

Two-stage proposal. Stage 1 ships immediately and is uncontroversial.
Stage 2 is conditional on V58 (adaptive sampling) or V56 (early-kill)
landing.

### Stage 1: exact distinct count via `frozenset`, no HLL

Edit `redcon/cmd/types.py` `GrepResult`:

```python
@dataclass(frozen=True, slots=True)
class GrepResult:
    matches: tuple[GrepMatch, ...]
    file_count: int
    match_count: int
    distinct_text_count: int   # NEW
```

Edit `redcon/cmd/compressors/grep_compressor.py` `parse_grep` and
`parse_grep_json` to compute `distinct_text_count` while accumulating
matches (one extra `set` insert per match, O(1) amortised). Add it to
the headline line:

```python
def _format_ultra(result):
    if result.match_count == 0:
        return "grep: no matches"
    if result.distinct_text_count < result.match_count:
        return (
            f"grep: {result.match_count} matches in {result.file_count} files "
            f"({result.distinct_text_count} distinct)"
        )
    return f"grep: {result.match_count} matches in {result.file_count} files"
```

Cost: ~10 LOC, zero new deps, zero risk to determinism. Token cost in
the *output*: +4 to +6 tokens on the header line, only when
distinct < match. On a 5000-match / 1000-distinct fixture that is a
trade of 6 tokens for one of the most informative integers the agent
can have.

### Stage 2 (conditional): HLL++ for streaming compressors

New file `redcon/cmd/_hll.py` (~60 LOC, stdlib only, fixed-seed
blake2b-derived hash; exposes `HLL(p=10)` with `.add(s: str)` and
`.estimate() -> int`). `parse_grep_streaming(byte_iter)` (introduced
by V58/V56/V59) accumulates into an `HLL` instead of a `set`. The
public `GrepResult.distinct_text_count` becomes "exact when buffered,
HLL estimate when streamed", with a `distinct_count_kind: Literal['exact',
'hll']` field so callers can tell. The schema declared in
`_meta.redcon` (per BASELINE convention, commit 257343) carries the
kind.

Pseudo-code for the registers:

```python
class HLL:
    __slots__ = ("p", "m", "regs")
    def __init__(self, p: int = 10) -> None:
        self.p = p
        self.m = 1 << p
        self.regs = bytearray(self.m)   # 8-bit per bucket; wastes 2 bits, no big deal
    def add(self, s: str) -> None:
        h = int.from_bytes(blake2b(s.encode("utf-8"), digest_size=8, key=_SEED).digest(), "big")
        idx = h & (self.m - 1)
        w = h >> self.p
        rho = (w | (1 << (64 - self.p))).bit_length() - (w.bit_length() if w else 0) + 1
        if rho > self.regs[idx]:
            self.regs[idx] = rho
    def estimate(self) -> int:
        # standard HLL with small/large-range corrections from Heule 2013, sec 4
        ...
```

Determinism: `_SEED` is a constant compiled in. No `random`, no clock.

## Estimated impact

- Token reduction: roughly **0** in the compressed output. Stage 1 costs
  ~6 tokens on the header line and gives the agent a strictly better
  summary. This is not a reduction vector; it is an information-density
  vector. The cost is tiny enough that quality_floor at COMPACT (>=30%)
  is unaffected on any realistic grep fixture.
- Latency: Stage 1 adds one `set.add()` per match. On a 5000-match parse
  that is microseconds, well below noise. Stage 2 HLL adds one
  blake2b-8 hash per match (~1 us each on a modern CPU); for 5000
  matches that is ~5 ms, also below noise compared to the subprocess
  itself.
- Affects: `grep_compressor.py`, `types.py::GrepResult`, golden fixtures
  for grep tests (must be regenerated). Cache key unaffected (input is
  argv, output schema does not feed back).
- Composes with V58 (adaptive sampling) and V56 (early-kill): under
  those, V52 stage 2 is the *only* way to keep a distinct count.
  Composes with V60 (rolling-hash dedup): both are streaming
  bounded-memory primitives; distinct-count is a strict sub-problem of
  shingle-dedup, but cheaper.

## Implementation cost

- Stage 1: ~15 lines including test fixture updates. Zero new deps.
- Stage 2: ~80 lines (`_hll.py` + parser plumbing + a kind discriminator).
  Stdlib only (`hashlib.blake2b`). Hashlib is already imported elsewhere
  in the project (used in `redcon/cmd/_codec_floor.py` if V04 lands;
  already in cache key paths). Risk to determinism: nil with fixed seed.
  Risk to must_preserve: nil; the patterns tuple in
  `GrepCompressor.must_preserve_patterns` is empty and the
  `verify_must_preserve` call would be unaffected. Risk to robustness
  fuzz (binary garbage / 5000 newlines / random word spam): zero, since
  HLL ingests only successfully-parsed match texts and the parser
  already guards malformed input.

## Disqualifiers / why this might be wrong

1. **Already covered by `set`**. For the eager parser, `set[str]` does
   the job in tens of bytes per distinct match, and 1e6 distinct lines
   is absurd for grep (the agent would be drowning anyway). This is the
   honesty point the prompt asks for: HLL on top of the current eager
   parser is engineering theatre. Stage 1 is the actual contribution.
2. **Distinct count may not be load-bearing for the agent**. The agent
   cares about *which file has which match*, which the existing per-file
   grouping already shows. A bare integer "1000 distinct" might never
   change agent behaviour. Counter: ULTRA tier emits only counts; here a
   distinct count is the difference between "5000 matches, mostly
   noise" and "5000 unique findings, expand". So the value is
   concentrated at ULTRA, where the rest of the per-match detail has
   been dropped.
3. **Streaming compressor may never ship**. V52 stage 2 is parasitic on
   V58/V56/V59. If those three are rejected (e.g. determinism concerns
   on adaptive sampling), HLL has nothing to enable.
4. **Floating-point determinism in HLL estimator**. Heule's correction
   curve uses log/exp which on different architectures can differ in
   the LSB of a double. To stay byte-deterministic across machines we
   either (a) skip the bias correction and accept ~3% extra error for
   small cardinalities, or (b) use the integer-arithmetic estimator
   from Ertl 2017 ("New cardinality estimation algorithms for
   HyperLogLog sketches"). Both are doable; both add code. The "use
   Python's float" path technically violates determinism on heterogenous
   deployment.
5. **Tiny absolute reduction**. Per the BASELINE bar ("breakthrough =
   >=5pp across multiple compressors"), V52 does not move that needle on
   its own. It is at best an enabler for V58.

## Verdict

- Novelty: **low** (stage 1 is a one-liner; stage 2 is a textbook
  algorithm, useful only as scaffolding for streaming work that has not
  been committed to)
- Feasibility: **high**
- Estimated speed of prototype: **2 hours** for stage 1 with golden
  fixture updates; **1 day** for stage 2 with deterministic
  integer-arithmetic estimator and tests
- Recommend prototype: **stage 1 yes, stage 2 conditional on V58 or V56
  landing**. Without a streaming compressor in the codebase, HLL is a
  solution looking for a problem; with one, it is the only deterministic
  O(1)-memory distinct counter and becomes load-bearing.
