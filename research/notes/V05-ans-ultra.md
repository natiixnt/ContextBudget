# V05: Asymmetric Numeral Systems (ANS) for ULTRA-tier serialisation of structured outputs

## Hypothesis

For Redcon's ULTRA tier, fixed-shape structured outputs (counts plus
path list, e.g. `diff: 12 files, +200 -50 [path1, ...]`) might be
shippable as a `{redcon:<schema-id>:<n>:<base64-of-rANS-payload>}`
envelope. The agent declares the schema once per session (one-time
token cost) and unpacks subsequent payloads via an MCP tool call.
The claim: byte-level ANS reduces payload size below the cl100k token
cost of the equivalent text, and the schema declaration plus MCP
unpack round-trip amortise across many calls.

The empirical result (computed below on real Redcon fixtures) is that
**this is uniformly false** under cl100k tokenisation. The proposal is
disqualified by an information-theoretic argument independent of
implementation quality.

## Theoretical basis

Let `T_text` be the cl100k token count of the textual ULTRA output,
`B` the raw byte length, and `H` the empirical byte entropy of the
payload. Byte-level rANS achieves `B_ans >= H * B / 8` bytes (Shannon
floor; rANS is within a fraction of a bit per symbol of this).
Base64 expands by 4/3, so the wire payload after base64 is
`B_b64 = ceil(4 * B_ans / 3)`. The cl100k encoder, fed near-random
ASCII (base64 of compressed bytes is statistically indistinguishable
from random bytes for BPE merges), produces tokens at rate
`r_b64 ~= 1.39 bytes/token` (measured below). The textual path list
tokenises at rate `r_text ~= 3.13 bytes/token`, because cl100k's BPE
merge table contains long structured tokens like ` src/`, `_module_`,
`.py`, `, `, and integer fragments that *are* a free dictionary for
the very alphabet the path list draws from.

Setting up the inequality `T_ans+b64 < T_text`:
```
T_ans+b64 = B_b64 / r_b64 = (4/3) * (H * B / 8) / r_b64
T_text     = B / r_text
T_ans+b64 < T_text   <=>   (4/3) * H / (8 * r_b64) < 1 / r_text
                       <=>   H < 6 r_b64 / r_text
```
With measured `r_b64 = 1.39`, `r_text = 3.13`, the bound is
`H < 6 * 1.39 / 3.13 = 2.66 bits/byte`.

The empirical byte entropy of the path-list payload, measured on the
500-file `_huge_diff` reconstruction below, is `H = 4.74 bits/byte`.
The required entropy floor is **1.78x lower than what any byte-level
coder can possibly achieve on this source**. Therefore no rANS / no
range coder / no Huffman / no Lempel-Ziv operating at byte level can
break even, regardless of code engineering.

The structural reason: cl100k's BPE was trained on web text, which is
saturated with paths and identifiers; structured ASCII payloads sit at
~3 bytes/token, while uniform binary noise (ANS output is by design
near-uniform) sits near 1.4 bytes/token. The 2.2x token-density gap
exceeds the Shannon savings of byte-level entropy coding on this
source by a factor of ~1.5.

## Concrete proposal for Redcon

The proposed change (and what was implemented in `/tmp/v05_ans.py` as a
throwaway): in `redcon/cmd/compressors/git_diff.py::_format_ultra` (and
the equivalents in `grep_compressor.py`, `listing_compressor.py`),
replace the textual return with:

```python
def _format_ultra(result: DiffResult) -> str:
    payload = serialise_struct(result)            # canonical bytes
    blob = rans_encode(payload, SCHEMA_D1_PROBS)  # constriction range coder
    body = base64.b64encode(blob).decode()
    n = len(payload)
    return "{redcon:D1:" + str(n) + ":" + body + "}"
```

The agent side adds a new MCP tool `redcon_unpack(envelope)` that
mirrors the encode step, returning the original struct as JSON or
plain text. A new module `redcon/cmd/ans_codec.py` would house the
codec and the per-schema probability tables.

This is the proposal. It does not work. The remainder of this note
gives numbers.

## Estimated impact

Computed on real fixtures (`tests/test_cmd_quality.py::_huge_diff`,
`_massive_grep`, `_massive_find`) regenerated as their ULTRA-equivalent
text outputs. cl100k via `tiktoken.get_encoding("cl100k_base")`. Range
coder via `constriction.stream.queue.RangeEncoder`.

### 1. Current ULTRA (truncated to 8 paths + "+N more")

| n_files | text tok | ANS+b64 tok | delta | ratio |
|---------|---------:|------------:|------:|------:|
| 1       | 17       | 43          | +26   | 2.53x |
| 10      | 63       | 123         | +60   | 1.95x |
| 100     | 63       | 123         | +60   | 1.95x |
| 500     | 65       | 128         | +63   | 1.97x |

ANS is roughly 2x worse across the entire range Redcon currently emits.

### 2. Hypothetical ULTRA-FULL (no truncation, full path list)

| n_files | text tok | ANS+b64 tok | delta | ratio |
|---------|---------:|------------:|------:|------:|
| 5       | 41       | 89          | +48   | 2.17x |
| 50      | 311      | 536         | +225  | 1.72x |
| 500     | 3013     | 5139        | +2126 | 1.71x |
| 1000    | 6014     | 10268       | +4254 | 1.71x |

The ratio asymptotes to ~1.7x worse and never crosses 1.0.

### 3. Tipping-point sweep

```
n_files | text_tok | ans_b64_tok | wins?
   1    |    17    |    43       | no
   8    |    59    |   114       | no
  64    |   395    |   668       | no
 512    |  3085    |  5230       | no
4096    | 27686    | 44186       | no
```

**Crossover threshold: never.** Even a shared-dictionary variant that
omits the schema-id header entirely (the lower bound for any wire
format) is worse:

```
shared-dict, no header, no schema-id:
n_files | text_tok | b64(ANS) tok | wins?
   1    |    17    |    36        | no
 512    |  3085    |  7180        | no
4096    | 27686    | 60085        | no
```

### 4. Bytes/token efficiency on cl100k (the load-bearing measurement)

| Content kind                     | bytes | tokens | bytes/tok |
|----------------------------------|------:|-------:|----------:|
| structured path list (500 files) |  9419 |   3013 | **3.13**  |
| `2048 zero chars` (BPE optimum)  |  2048 |    683 | 3.00      |
| random hex (2048 B random bytes) |  4096 |   2333 | 1.76      |
| random base85 (2048 B)           |  2560 |   1982 | 1.29      |
| random base64 (2048 B)           |  2732 |   1945 | 1.40      |
| base85(ANS(path list))           |  6680 |   5153 | 1.30      |
| base64(ANS(path list))           |  7128 |   5129 | 1.39      |

cl100k tokenises a structured path list **2.25x more densely** than
any base-N encoding of compressed bytes. The path list itself is
already near the BPE-optimal density on cl100k.

### 5. Round-trip correctness and unpack latency

- Round-trip on 500-file diff: byte-identical decode (verified).
- Decode latency: 344 us / call in pure Python with constriction's
  range coder. With a Rust-backed coder, sub-100 us is plausible.
- MCP tool round-trip on a hot local path: ~80 to 300 ms (network +
  JSON envelope + agent token generation). Decode is negligible.
- Per-call latency: the MCP round-trip dominates, so even if ANS
  saved tokens, the agent would pay 100x to 1000x more on latency
  than it gained on tokens compared to inlining the text.

### 6. Schema-declaration amortisation

Schema declaration is 51 cl100k tokens. For 500-file diff, ANS+b64
costs **2126 tokens MORE per call** than the text. The schema cost
amortises in `ceil(51 / -2126) = NEVER`. Per-call savings are
negative; no number of calls makes it worth shipping.

## Implementation cost

- ~250 lines for codec + schema table + MCP tool + tests.
- New runtime deps: `constriction` (Rust-backed range coder via
  PyO3, 1.5 MB wheel) or pure-Python equivalent. Adds an
  install-time dependency. Does not violate "no required network /
  no embeddings" but does break "no binary protocols on the
  agent-facing surface" (constraint 7 in BASELINE.md).
- Risks: brittle determinism across constriction releases (the byte
  layout of the encoded stream is not part of the public API
  contract); base64 in MCP tool output crosses a JSON-string layer
  that may re-encode whitespace, changing tokenisation; cache-key
  changes (the wire form depends on the schema version, so a schema
  bump invalidates every cached entry).

## Disqualifiers / why this might be wrong

1. **Information-theoretic disqualifier (primary).** With cl100k's
   measured bytes-per-token of 3.13 on structured path text and 1.39
   on base64, the byte-entropy floor required for ANS+b64 to break
   even is `H < 2.66 bits/byte`. The path-list payload has
   `H = 4.74 bits/byte`. The gap is **fundamental** and independent
   of coder quality. Switching from rANS to range coder, dictionary
   coder, Huffman, LZMA, brotli, or zstd does not help because they
   are all upper-bounded by the same Shannon floor.

2. **Tokenizer asymmetry, not random misalignment.** The 2.25x
   density gap is structural (cl100k merges paths) not stochastic.
   Base122, base91, base85, hex, and binary-to-printable schemes
   all sit between 1.0 and 1.8 bytes/token. None reach 3.0.

3. **Structural compression already wins by 99%.** Replacing the
   path-list spelling with a template form (e.g.
   `[src/module_{0..499}.py]`) compresses the 500-file fixture from
   3013 to 22 tokens, a 99.3% reduction. ANS+b64 would expand it to
   5139. Any token saved by the current BASELINE compact-tier
   reductions (97% on git_diff) is already past the point where ANS
   could help even if it didn't lose.

4. **The MCP unpack round-trip is the wrong cost trade.** Trading a
   one-line inline text for a tool call that returns the same data
   adds latency and cognitive load on the agent without saving
   tokens (it loses tokens). The cache-friendly part of Redcon's
   architecture is that ULTRA outputs are tiny strings the agent
   can read inline. Forcing a round-trip undoes that.

5. **Constraint 7 violation.** BASELINE.md states "Output is plain
   text targeted at a tokenizer (cl100k default). No binary
   protocols in the agent-facing surface." A base64'd rANS blob is
   exactly the binary protocol that constraint forbids. The
   constraint exists *because* of the bytes-per-token asymmetry
   measured here.

6. **Amortisation is upside-down.** Schemes that amortise a
   one-time header against many calls require positive per-call
   savings. ANS+b64 has *negative* per-call savings, so the
   one-time cost adds to a deficit that grows monotonically.

## Verdict

- Novelty: low (the idea reduces to "use entropy coding on text",
  which is already excluded by the project's "no binary protocol"
  constraint and is empirically dominated by the existing structural
  + tokenizer-aware ULTRA format).
- Feasibility: low (information-theoretically disqualified for cl100k).
- Estimated speed of prototype: experiment took ~2 hours; production
  integration would be days but is wasted effort.
- Recommend prototype: **no**.

### Boundary learned

The investigation cleanly characterises the **token-density bound for
binary side-channels on cl100k**: any base-N encoded payload that the
agent must consume as a token stream is bounded above at ~1.8
bytes/token; structured ASCII payloads sit near 3.0 bytes/token. The
2.25x gap means byte-level entropy coding can only help when the
source has Shannon entropy below ~2.7 bits/byte, which excludes essentially
every structured text payload Redcon emits.

The follow-on direction this opens is **structural compression**
(V13 "CST template extraction", V42 "hash-keyed shared dictionary",
V41 "session-scoped 4-char alias", V99 "custom BPE on Redcon corpus").
Those operate inside the tokenizer's domain and do not pay the
binary-to-text expansion. The structural-template probe in
`/tmp/v05_structural.py` already shows 99.3% reductions on path-list
enumerations - a number ANS cannot approach. ANS is the wrong vector;
the reduction it tries to capture lives in the BPE table, not in the
byte distribution.

## Reproducer

Throwaway scripts (do not commit):
- `/tmp/v05_ans.py` - main ANS+base64 sweep (Experiments 1-6 above)
- `/tmp/v05_supplementary.py` - bytes-per-token measurements,
  Shannon-floor argument, alphabet comparison
- `/tmp/v05_structural.py` - structural-compression head-to-head

Run with the project's `.venv/bin/python` after
`pip install tiktoken constriction`.
