# V93: Proof-carrying compression - attach hash certifying invariant set preserved

## Hypothesis

Today the `must_preserve_ok` boolean on `CompressedOutput` says "the
compressor's regex set matched the formatted text". That is a single bit
produced and consumed by the same process. A downstream consumer (CI,
another agent, a third-party harness, a cache replay) cannot independently
re-verify that bit without re-running the full quality harness, which means
re-decoding, re-parsing, and re-applying every regex to either the raw
output or a reference fixture. There is no compact, cryptographically
verifiable certificate that travels with the compressed text.

Claim: emit a deterministic SHA-256 over the sorted tuple of
`(must_preserve_pattern, capture)` facts that the compressor proved present
in the **raw** input AND re-verified in the **compressed** output. Ship that
hash on `CompressedOutput.notes` and inside the `_meta.redcon` block. A
consumer with the compressed text and the published fact list (or the
ability to compute it from the schema's `must_preserve_patterns` plus the
text) can recompute the hash in O(facts) regex passes and confirm the
producer kept its end of the contract. Bytes added: ~16 cl100k tokens
(`mp_sha=<8 hex>=`). Token saving: zero. Value: an audit trail tied to
the cryptographic identity of the must-preserve set, with no new
network and no new model.

This vector is explicitly a TRUST/AUDITING feature. It does not move
the compact-tier reduction by a single point. The right place to evaluate
it is governance, not headline benchmarks.

## Theoretical basis

### Setup

Let `P = (p_1, ..., p_k)` be the schema's `must_preserve_patterns`
tuple (already a frozen attribute on every compressor). Let `R` be the
raw input text and `C` the formatted compressed text. For each pattern
`p_i` define the **fact set** of `R`:

  `F_i(R) = sorted({ m.group(0) | m in re.finditer(p_i, R, MULTILINE) })`.

Some patterns are existence-only (e.g. `"diff --git"` in `git_diff`),
others carry identifying captures (test names, file paths). Either way
`F_i(R)` is a finite, deterministically ordered tuple of byte strings.

Define the **invariant certificate** as

  `cert(R) = SHA256( serialise( ((p_1, F_1(R)), ..., (p_k, F_k(R))) ) )`

where `serialise` is a fixed canonical encoding (e.g. length-prefixed
UTF-8 bytes joined with a single NUL separator - no JSON, no whitespace
ambiguity). `serialise` and the sort order are part of the contract.

### What the producer proves

Today `verify_must_preserve(C, P, R)` (in `redcon/cmd/compressors/base.py`)
returns True iff `for all i: F_i(R) != () => F_i(C) != ()`. That is the
**existence** half of the invariant. Strengthen it to the **set** half:

  `must_preserve_set(C, P, R) := for all i: F_i(R) is a subset of F_i(C)`.

Existence-only patterns trivially collapse to existence (the set has one
element, the literal match). Capture-bearing patterns require every named
fact present in `R` to also be present in `C`. This is what a
"proof-carrying" claim should certify.

The producer emits

  `tag = cert(R)`

iff `must_preserve_set(C, P, R)` holds. The producer will not emit a tag
on a failing check; absence-of-tag is an explicit "no certificate".

### What the consumer can check

Given `C` and `tag`, the consumer recomputes

  `cert(C) = SHA256( serialise( ((p_i, F_i(C)),...) ) )`

and asserts

  `cert(C) == tag`.

This holds iff `F_i(C) supseteq F_i(R)` AND `F_i(C) subseteq F_i(R)`,
i.e. `F_i(C) == F_i(R)`. So the certificate is even stronger than the
producer-side check: it certifies *equality of the must-preserve fact
set across compression*. This rules out two failure modes the current
boolean does not catch:

  1. **Spurious additions.** A compressor that hallucinates a fact
     (e.g. inserts a synthetic file path that wasn't in `R`) currently
     passes `must_preserve_ok` because every required match still
     fires. With cert equality, `F_i(C)` would gain an element not in
     `F_i(R)`, hash mismatches, audit fails.
  2. **Silent fact drops in capture-bearing patterns.** If the regex
     uses alternation and `R` had two failing tests but `C` keeps only
     one, today both `re.search` calls return non-None and the boolean
     passes. Set equality fails.

### Soundness / completeness

  - **Sound** (no false positives): if `cert(C) == cert(R)` then for
    every regex `p_i`, the multiset of matches in `C` equals the
    multiset in `R` (under the canonical sort). Collisions require
    SHA-256 second-preimage, ~2^256 work.
  - **Complete relative to the regex set**: the certificate is only as
    expressive as `must_preserve_patterns`. Facts not captured by a
    regex are not certified. This is the same limitation the existing
    boolean already has; we inherit it, not introduce it.
  - **Deterministic**: SHA-256 is, sort is, regex iteration order is.
    Same `(P, R)` -> same `tag`. Preserves BASELINE constraint #1.

### Cost arithmetic (the >= 3 lines of math)

Token cost per output:
  - tag string: `mp_sha=<16 hex chars>;` = 22 bytes = ~6 cl100k tokens
    (truncated to 64 bits is enough for audit; full 256 bits costs
    ~16 tokens). Even a 0.001% chance of false-accept across 10^6
    artefacts requires only 64 bits per Birthday bound:
    `P_collision ~ N^2 / 2^(b+1)`, set N=10^6, b=64 -> P ~ 10^12 / 2^65 ~ 3e-8.
  - producer compute: one extra `re.findall` per pattern on `C` (already
    done conceptually for verification) + `bytes.join` + one SHA-256.
    For a typical compressed output (<500 bytes) this is sub-microsecond.
  - consumer compute: identical; `O(|C| * sum |p_i|)` regex + 1 hash.

Token saving: 0. Latency cost: <5 microseconds amortised over the
existing compressor pass. Value: cryptographic equality on the
fact set. This is a Pareto-orthogonal improvement: it moves the
auditing axis without moving the compression axis.

### Prior work / framing

This is the standard **proof-carrying code** pattern (Necula 1997)
applied to lossy compression: producer emits an artefact accompanied
by a checkable witness. In our setting the witness is a hash and the
property is "set of must-preserve matches identical between source
and target". Closely adjacent: Merkle authenticated data structures
(here we have a flat hash, not a tree), and signed-build provenance
(SLSA / in-toto). Difference: those frameworks sign **identity** of
the artefact; we hash **semantic invariants** of it. Cheaper, weaker,
and the right tool for this specific contract.

## Concrete proposal for Redcon

### Files

  - `redcon/cmd/compressors/base.py` (modify, ~30 LOC):
    - Add `_extract_facts(P, text) -> tuple[tuple[str, tuple[bytes, ...]], ...]`.
    - Add `compute_invariant_cert(P, text) -> str` (returns 16-hex truncated
      SHA-256).
    - Add `verify_must_preserve_set(C, P, R) -> bool` that implements the
      stronger set-equality check (the current `verify_must_preserve` stays;
      this is an opt-in stronger check used by the cert emission path).
  - `redcon/cmd/quality.py` (modify, ~10 LOC):
    - In `run_quality_check`, after the existing `must_preserve_ok`
      assertion, compute `cert(R)` and `cert(C)`; record both on the
      `LevelReport` via a new optional field `cert_match: bool`. Quality
      harness fails if cert disagreement at COMPACT/VERBOSE.
  - `redcon/cmd/types.py` (no schema break): the SHA goes into the
    existing `notes: tuple[str, ...]` field as a string entry like
    `"mp_sha=ab12cd34ef567890"`. Consumers parse on the literal prefix.
  - `redcon/mcp/tools.py` (modify, ~5 LOC): in `tool_run` and
    `tool_quality_check`, when a `notes` entry begins with `mp_sha=`,
    surface it inside `_meta.redcon` as `must_preserve_sha=<hex>`.
    This honours the existing `_meta.redcon` convention (commit 257343)
    rather than inventing a parallel block.
  - Per-compressor `compress()` methods: zero-line change. They already
    pass `notes=ctx.notes`. The cert is appended by a small wrapper in
    the pipeline so individual compressors don't each duplicate the call.

### Pipeline integration

```python
# redcon/cmd/pipeline.py (sketch, ~10 LOC inserted after compressor.compress(...))
out = compressor.compress(raw_stdout, raw_stderr, ctx)
if out.must_preserve_ok and out.level != CompressionLevel.ULTRA:
    # ULTRA is exempt from must-preserve by design (BASELINE.md line 30),
    # so we do not certify a fact set we know is incomplete.
    cert = compute_invariant_cert(
        compressor.must_preserve_patterns,
        # certify equality between raw and compressed; mismatch -> no tag
        raw=raw_stdout.decode("utf-8", errors="replace"),
        compressed=out.text,
    )
    if cert is not None:
        out = replace(out, notes=out.notes + (f"mp_sha={cert}",))
```

```python
# redcon/cmd/compressors/base.py
def _extract_facts(patterns, text):
    """Returns ((pattern, sorted_unique_matches), ...). Deterministic."""
    out = []
    for pat in patterns:
        regex = re.compile(pat, re.MULTILINE)
        matches = sorted({m.group(0).encode("utf-8") for m in regex.finditer(text)})
        out.append((pat.encode("utf-8"), tuple(matches)))
    return tuple(out)

def compute_invariant_cert(patterns, *, raw, compressed):
    raw_facts = _extract_facts(patterns, raw)
    cmp_facts = _extract_facts(patterns, compressed)
    if raw_facts != cmp_facts:        # set-equality check
        return None
    blob = b"\x00".join(
        len(p).to_bytes(4, "big") + p +
        b"".join(len(m).to_bytes(4, "big") + m for m in matches)
        for p, matches in raw_facts
    )
    return hashlib.sha256(blob).hexdigest()[:16]    # 64 bits, ~6 tokens
```

The 16-hex truncation is a deliberate token-economy choice. The
contract sites the truncation in the docstring; clients that want
full 256-bit collision resistance can flip a flag and pay 32 bytes
instead.

### Interaction with cache and `_meta.redcon`

  - Cache: the certificate is a **function of** `(P, raw, compressed)`,
    all of which the cache already keys (or stores). A cache hit returns
    the same `out.notes`, so the same cert. Determinism preserved
    (BASELINE constraint #6).
  - `_meta.redcon` block (per `redcon/mcp/tools.py::_meta_block`): we
    extend with one optional field `must_preserve_sha`. Backward
    compatible because consumers read `_meta.redcon[*]` by name.

## Estimated impact

  - **Token reduction**: 0. This is not a compression vector; it is a
    trust vector. We add ~6 cl100k tokens per output that has a
    certificate. On the 11 existing compressors at COMPACT, that is
    a ~1-2% relative inflation on the smallest fixtures and noise on
    large ones (where the existing reduction is 70-97%).
  - **Latency**: cold +0 (uses stdlib `hashlib`, `re` already imported).
    Warm +1-3 microseconds per output (one extra regex sweep over `C`
    plus one SHA-256 over <2 KiB - both fast on CPython).
  - **Affects**: `redcon/cmd/pipeline.py` (one new call), `quality.py`
    (extra check in harness), `redcon/mcp/tools.py` (surface in `_meta`).
    No scorer touched. No cache layer touched.

## Implementation cost

  - Lines of code: ~80 total. ~30 in `base.py`, ~10 in `pipeline.py`,
    ~10 in `quality.py`, ~5 in `mcp/tools.py`, ~25 of tests.
  - New runtime deps: none. `hashlib`, `re`, `dataclasses.replace` are
    stdlib. Does not violate "no required network / no embeddings".
  - Risks:
    - **Determinism**: regex iteration order over a fixed pattern is
      already deterministic (left-to-right scan). Sort + canonical
      encoding eliminate any residual ordering ambiguity. SHA-256 is
      deterministic.
    - **Robustness**: the cert is computed *after* the compressor
      finishes. A pathological raw input that crashes the compressor
      is already handled by the existing robustness harness; cert
      computation only runs on successful compressions, so it does
      not introduce new crash surface. We catch any regex `error`
      and emit no cert (the boolean already governs the same path).
    - **Must-preserve semantics**: the cert is *stronger* than the
      existing boolean (set equality vs existence). It will refuse
      to emit on cases the boolean accepts (e.g. "all required
      patterns matched but capture set differs"). Mitigation: the
      old boolean stays the gate for `must_preserve_ok`; the cert
      is *additional* metadata. The harness flips a stricter flag,
      but it does not retroactively fail existing fixtures unless we
      *also* tighten the floor - which we explicitly do not propose
      here. (A follow-up vector could.)
    - **Truncation collision**: 64-bit SHA prefix has ~3e-8 collision
      probability at 10^6 artefacts. For an audit trail this is fine;
      for a security boundary it is not. The contract pins the
      truncation length; downstream readers either accept 64 bits
      or request the full 256 by setting a flag.

## Disqualifiers / why this might be wrong

  1. **Already-implemented in disguise.** `must_preserve_ok` exists and
     the `_meta.redcon` block is already a thing (commit 257343). What
     V93 adds over the boolean is *cryptographic* equality of the fact
     set, not just "did every regex match somewhere". Honest assessment:
     for many existence-only patterns (e.g. `git_diff`'s
     `r"\bfiles? changed\b|\bdiff --git\b|^[A-Z] [^\s]+|^- ?[^\s]+"`)
     the cert collapses to "the same set of literal sentinels appeared",
     which is *already* what the boolean approximates. The genuinely
     new value is on capture-bearing patterns - test names, file paths,
     line numbers in failures - where the boolean accepts a partial
     drop.
  2. **Wrong layer for trust.** A motivated adversary in the producer
     process can compute any hash they want; `cert(C)` proves nothing
     about a malicious compressor. The certificate is a self-check
     against accidental regression, not an adversarial signature.
     Anyone wanting real provenance needs an HMAC keyed by a secret
     the consumer shares, or an attestation chain. We deliberately
     stay at the "honest producer audits itself" level. Anyone reading
     the verdict expecting Sigstore-grade guarantees will be
     disappointed.
  3. **Set equality is too strict for ULTRA.** ULTRA is allowed to drop
     facts. A cert at ULTRA either has to be opt-out (we propose this)
     or relax to a subset hash (`F_i(C) subset F_i(R)`), which is no
     longer commutative and breaks the symmetry argument. We chose
     opt-out: ULTRA carries no certificate, only a level marker, and
     the consumer knows from `level == "ultra"` not to expect one.
     This means V93's value is concentrated on COMPACT and VERBOSE -
     the same tiers where the boolean is enforced today.
  4. **Token cost is small but non-zero.** ~6 tokens on each
     COMPACT/VERBOSE output. On a 12-token output (small `git_status`)
     that is 50% inflation. The harness already exempts <80-token
     inputs from reduction floor checks, and we should plumb the same
     threshold here: no certificate on inputs the harness doesn't
     measure compression on. Mitigation built into the proposal.
  5. **Quality harness is the right tool already.** The
     `redcon_quality_check` MCP tool already lets a caller re-run the
     harness over an arbitrary command. If you can run the harness,
     you don't need a hash. The cert exists for the case where
     the consumer has *only* the compressed text + metadata and
     cannot replay the original command (it ran on a different host,
     the working tree has moved on, the binary is no longer
     installed). That is a real but narrow audience: cache replays,
     archived agent traces, multi-tenant retrieval logs.

## Verdict

  - **Novelty: low**. SHA-256 over a sorted fact list. The
    proof-carrying-code framing is forty years old; the math is
    elementary. The only original choice is *what* to hash
    (regex-anchored fact sets) and *where* to put it (existing
    `notes` field plus `_meta.redcon`). No new theory, no new
    algorithm, no new data structure. As BASELINE.md asks for
    explicit boundaries: this is a TRUST/AUDIT feature, not a
    compression breakthrough. Mark accordingly.
  - **Feasibility: high**. ~80 LOC, stdlib only, no determinism
    risk, no cache invalidation, no embedding rule violation,
    no network dep. Slots into pipeline immediately after the
    existing must-preserve check.
  - **Estimated speed of prototype: half a day to one day**. Implement
    `_extract_facts` and `compute_invariant_cert` (1 hour), wire into
    pipeline + tests (2 hours), surface in `_meta.redcon` (30 min),
    add property test that perturbing any single fact in `R` flips the
    hash (1 hour). The harness change to fail on cert disagreement is
    another hour with conservative defaults (warn-only first, fail-hard
    after a release).
  - **Recommend prototype: yes, conditional on framing it as audit
    metadata, not a compression result**. The token budget is real
    (~6 tokens / output) and the win is non-token (verifiable
    invariants for downstream consumers). It is worth integrating
    *only* with the existing `_meta.redcon` convention - inventing a
    parallel block would be wrong. Most natural delivery: one PR
    against `redcon/cmd/compressors/base.py` + `pipeline.py` +
    `quality.py` + `mcp/tools.py`, plus a CI test that round-trips
    the cert through `redcon_run` and re-verifies it from the
    response payload alone.
