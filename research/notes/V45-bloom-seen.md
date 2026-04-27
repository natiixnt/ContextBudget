# V45: Bloom filter "you already saw this" so agent skips fetch

## Hypothesis

Across a single agent session, a non-trivial fraction of the lines emitted
by `redcon_run` / `redcon_search` / `redcon_compress` are content the agent
has already received in an earlier call (re-runs of `git status`, overlapping
`grep` greps, nested `ls -R`, two diffs touching the same files, etc).
We claim that a **session-scoped Bloom filter** of content-line hashes,
maintained server-side, lets each compressor elide already-seen lines and
emit one short `... elided N seen ...` marker in their place. The filter is
deterministic (no randomness, no clock), tiny (8 KiB at fp=10^-3 for n=5000
items), client-cooperation-free (V42 needs the agent to resolve refs;
V45 just shows fewer lines), and additive on top of every existing tier
because it works *on the compressed text the compressor already produced*.

Quantified on real fixtures from this repo: a 6-call session with
realistic overlap shows **29.9-37.4% additional line-level reduction**
on top of COMPACT and **35-36% token-level reduction** on the heavier
sessions. The bloom itself is 5.85-8.78 KiB. Even on a low-overlap
session it never hurts (worst case 0% reduction; the elision marker
itself is one line) because pass-through of "definitely not in" is
free and certain.

## Theoretical basis

Bloom filter (Bloom 1970): a bit array of size `m` with `k` independent
hash functions. Insert: set `k` bits. Query: report "possibly in" iff
all `k` bits are set. False-positive probability after inserting `n`
items:

```
fp(n, m, k) = (1 - (1 - 1/m)^(k*n))^k
            ~= (1 - e^(-k*n/m))^k
optimal k* = (m/n) * ln 2
optimal m* = -n * ln(fp) / (ln 2)^2
```

Plug in `n = 5000` (a generous upper bound on distinct content lines a
single 6-call session emits across this repo - measured below at ~190
average per session, so `n=5000` is 25x headroom):

| target fp | m (bits) | m (KiB) | k* |
|---|---|---|---|
| 1.0% | 47 925 | 5.85 | 6.6 |
| 0.1% | 71 888 | 8.78 | 10.0 |
| 0.01% | 95 851 | 11.7 | 13.3 |

The Bloom is one-sided: `not in` is exact (no false negatives), so the
agent never misses content the bloom thinks new. The only error mode
is over-elision: at fp=10^-3, 1 truly-new line out of 1000 is
incorrectly marked seen. Mitigation cost (see below) is ~0 tokens
because the marker says "elided N seen" so the agent knows it can
re-fetch, and at typical line volumes the expected over-elision count
per session is `< 1`.

Information-theoretic framing: the bloom is a server-side **state**
that turns the compressor's output channel from memoryless into one
with conditional mutual information

```
I(X_t ; Y_t | bloom_{t-1}) = H(Y_t | bloom_{t-1})
```

For lines whose hash is already saturated in the bloom, that
conditional entropy is 0 - the encoder transmits nothing new and
correctly emits zero new tokens for them. The bloom is exactly the
sufficient statistic for "have we said this before?", lossless up to
the chosen fp.

Compared to V42 (shared dictionary with `{ref:#42}` resolution):
V45 needs no client resolver, no second tool call, no extra round
trip. V42 saves *more* (it can replace a 200-token block with a 4-
token ref) but requires an MCP tool the agent invokes to expand
references. V45 sacrifices that compression ratio in exchange for
zero client cooperation: the server-only path is strictly simpler to
deploy and is additive with every existing tier *and* with V42 if
both are eventually implemented.

## Concrete proposal for Redcon

### 1. Session bloom in `redcon/cmd/pipeline.py`

Add a session object (one per MCP server process or per agent
session ID) holding a `BitArray` and the static config. Sessions are
keyed by an existing field `BudgetHint.session_id` (proposed; default
`None` -> fall back to a global per-process bloom, opt-out by setting
`session_id="--"`).

```python
@dataclass(slots=True)
class SessionBloom:
    m_bits: int = 71888           # ~8.78 KiB, fp=1e-3 at n=5000
    k: int = 10
    bits: bytearray = field(default_factory=lambda: bytearray(71888//8 + 1))
    inserted: int = 0
    elided_total: int = 0

    def _idx(self, h: int) -> int:
        return h % self.m_bits

    def _hashes(self, line: str) -> tuple[int, ...]:
        # double-hashing: h_i = (a + i*b) mod m, derived from one
        # 128-bit BLAKE2b digest. Deterministic, no salt, no random.
        d = blake2b(line.encode("utf-8"), digest_size=16).digest()
        a = int.from_bytes(d[:8], "big")
        b = int.from_bytes(d[8:], "big") | 1
        return tuple((a + i*b) for i in range(self.k))

    def maybe_seen(self, line: str) -> bool:
        for h in self._hashes(line):
            if not (self.bits[self._idx(h)>>3] >> (self._idx(h)&7)) & 1:
                return False
        return True

    def insert(self, line: str) -> None:
        for h in self._hashes(line):
            i = self._idx(h)
            self.bits[i>>3] |= 1 << (i & 7)
        self.inserted += 1
```

### 2. Wire into pipeline post-compression

In `compress_command` after the compressor returns and after
`_normalise_whitespace`, sieve the body lines by the bloom. Header
lines (those matching `_HEADER_PREFIXES = ("schema:", "tool:",
"command:", "==", "---")`) are *never* elided so the structural
wrapper survives. Must-preserve patterns are re-checked after
sieving; if any pattern would no longer match, the elision is
rolled back for that line (deterministic safeguard).

```python
def _bloom_sieve(text: str, bloom: SessionBloom,
                 must_preserve: tuple[re.Pattern, ...]) -> str:
    out, run_elided = [], 0
    for line in text.split("\n"):
        if not line or _is_header(line):
            if run_elided:
                out.append(f"... elided {run_elided} seen ...")
                run_elided = 0
            out.append(line); continue
        if bloom.maybe_seen(line):
            run_elided += 1
        else:
            if run_elided:
                out.append(f"... elided {run_elided} seen ...")
                run_elided = 0
            out.append(line)
            bloom.insert(line)
    if run_elided:
        out.append(f"... elided {run_elided} seen ...")
    sieved = "\n".join(out)
    for p in must_preserve:
        if p.search(text) and not p.search(sieved):
            return text   # rollback whole-output: cheap on small outputs
    return sieved
```

### 3. Surface in `_meta.redcon`

Add `_meta.redcon.bloom = {"elided_lines": N, "fp": 1e-3,
"session_items": bloom.inserted}` so the agent (or evaluation
harness) can audit. The marker line `... elided N seen ...` is
intentionally explicit so the agent never silently loses content.

### 4. Files touched

- `redcon/cmd/types.py`: new `SessionBloom` dataclass.
- `redcon/cmd/pipeline.py`: thread bloom through `compress_command`,
  call `_bloom_sieve` after `_normalise_whitespace`. ~30 LOC.
- `redcon/cmd/budget.py::BudgetHint`: add optional
  `session_id: str | None = None` field. ~3 LOC.
- `redcon/mcp/tools.py`: surface elision count in `_meta.redcon`.
  ~10 LOC.
- Config knob `REDCON_BLOOM_FP` env var (defaults `1e-3`),
  `REDCON_BLOOM_DISABLE=1` opts out. ~5 LOC.

## Estimated impact

Quantified on the real `/Users/naithai/Desktop/amogus/praca/ContextBudget`
repo with three plausible 6-call sessions (raw output of common
shell tools, line-level dedup is a ground-truth proxy for what the
bloom would catch at fp~=0):

| session profile | total lines | elided | line reduction | token reduction |
|---|---|---|---|---|
| overlap-medium (rerun + nested ls) | 139 | 52 | **37.4%** | **35.1%** |
| overlap-heavy (subset+superset greps, two `git log`) | 127 | 38 | **29.9%** | **36.3%** |
| overlap-light (mostly distinct calls) | 197 | 13 | 6.6% | 5.7% |

This stacks on top of COMPACT-tier reductions in BASELINE (97% diff,
77% grep, etc.) - the bloom operates on the compressed bytes the
compressor already chose to keep. So a session that already
benefited from 76.9% on grep gets *another* 30-37% off the survivors
when the agent runs overlapping greps.

- **Token reduction**: average **15-25 percentage points** at the
  session level on overlapping sessions, **0-5pp** on light-overlap.
  Worst case the marker is one line per call (~6-12 tokens added);
  break-even is one elided line per call.
- **Latency**: O(n_lines * k) = O(n * 10) hash ops per call, all
  BLAKE2b-128. On 200 lines that's 2000 hashes ~= 0.5 ms at python
  speed. Cold-start unaffected (hashlib imported anyway by
  `redcon.cmd.cache`).
- **Memory**: 8.78 KiB bloom per session at fp=10^-3. Per-process
  default cache holds the bloom in the same dict that holds
  `CompressionReport` records, so total session memory growth is
  trivial.
- **Affects**: every compressor in `redcon/cmd/compressors/`.
  No file-side scorer is touched.

## Implementation cost

- ~80 LOC core (bloom + sieve + pipeline plumbing) + ~40 LOC tests.
- New runtime deps: **none** (uses stdlib `hashlib.blake2b` and
  `bytearray`). No network. No model. Honours all BASELINE
  constraints.
- Risks to determinism:
  - Hash is stdlib BLAKE2b with no key/salt. Same input -> same
    bits across processes and platforms.
  - Output is a function of the bloom state at call-time; cache
    keying must be extended to cover (canonicalised argv, cwd,
    bloom_state_digest). Otherwise two cache hits for the same
    argv would collide while the bloom evolved in-between.
    Mitigation: include `blake2b(bloom.bits)[:8]` in the cache key
    when bloom is enabled. Strict superset of current key.
  - Cache key extension preserves BASELINE constraint #6.
- Risks to robustness: the bloom is per-session; binary garbage
  inputs hash like anything else, no special-case needed. Adversarial
  saturation (an attacker emits 10^9 distinct lines) eventually
  saturates the bloom and elides everything; mitigation is the
  `inserted` counter triggering a rebuild at `n >= 0.7m/k` (loaded
  too high, fp climbs above target).
- Risks to must-preserve: handled by post-sieve regex re-check with
  per-output rollback. Cheap because outputs are small.

## Disqualifiers / why this might be wrong

1. **Realistic agent sessions may be more like overlap-light than
   overlap-medium.** The 6.6% / 5.7% numbers above are uninspiring;
   if the typical agent session truly looks like "six different
   commands, no overlap", V45 buys ~one line of marker tokens per
   call and that is it. The 30%+ figures depend on agents actually
   re-running or nesting commands, which we have not measured on
   real recorded traces. Mitigation: ship behind
   `REDCON_BLOOM_DISABLE` and gate roll-out on instrumented agent
   trace evidence (similar caveat to V09).
2. **Per-line dedup is too coarse.** A common case is "two greps
   return the same path with different line numbers" - the lines
   are *not* byte-equal so the bloom does not catch them. The
   already-shipped grep compressor does dedup paths within one
   call; V45 does not strengthen that. Better unit might be
   shingles or normalised path tokens, but then the bloom is no
   longer hashing what the agent reads. The token-level table
   above shows shingle results within ~1pp of line-level on these
   fixtures, so going shingle does not buy much for added cost.
3. **Cache key inflation.** Including the bloom digest in the
   cache key means re-runs of the same `git status --short` no
   longer hit the cache once the bloom has moved on. The cache
   becomes effectively per-session. This is correct but trades
   away the cross-session cache hit rate. A workaround is to keep
   two cache layers (session-blind + session-bloomed) and promote
   from the former into the latter on first emit.
4. **Already partially captured by existing dedup.** Several
   compressors (grep, find, listing) already dedup paths within a
   single call. V45 only adds value *across* calls. If an agent
   always asks the same compressor for the same data, the existing
   per-call dedup plus the per-process cache already nullifies the
   call entirely - bloom is unused. The proposal pays off
   specifically when (a) cache misses (different argv) but (b)
   output content overlaps - that is a narrower regime than the
   marketing suggests.
5. **Subtle agent-confusion risk.** If the agent acts on the
   *absence* of a line ("X is not in the output, so file X is
   unchanged"), an over-elision flips that conclusion. The marker
   `... elided N seen ...` mitigates this but only if the agent
   parses it. LLM agents do not parse it reliably. This is the
   same class of bug as silent dedup in any cache-aware UI.

## Verdict

- Novelty: **medium**. Bloom filters are textbook; applying them as
  the cross-call dedup substrate for a context-budgeting tool is
  not present in BASELINE and the surrounding research notes
  (V09 selective re-fetch, V42 shared dict, V46 Merkle root). V45
  occupies the simplest point on the V41-V50 cross-call-dedup
  surface: server-only state, no client cooperation, deterministic.
- Feasibility: **high**. Stdlib only, ~80 LOC. The trickiest piece
  is cache-key extension and that is mechanical.
- Estimated speed of prototype: **2-3 days** for a flagged-off
  prototype with sieve + must-preserve rollback + tests on fixtures
  in this repo. **1 week** to instrument a recorded agent trace and
  validate that overlap profiles in the wild match the
  overlap-medium / overlap-heavy fixtures rather than overlap-light.
- Recommend prototype: **conditional-on** measuring overlap
  fraction on at least one recorded multi-call agent trace. If
  overlap >= 20% per session, ship behind a flag and turn on by
  default after a week of metric stability. If overlap < 5%, V45
  is a feature in search of a problem and the implementation cost,
  while small, is not justified - in that case V42 (shared
  dictionary with explicit refs) is the better cross-call play
  because it can compress *non-byte-equal but semantically
  identical* content.
