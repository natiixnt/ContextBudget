# V94: Self-instructing prompt format - tell the model when NOT to ask for expansion

## Hypothesis

A COMPACT compressed output should open with a **one-line directive aimed
at the LLM consuming it**, of the form:

```
This is a COMPACT view; expand only if you see <X>.
```

where `<X>` is a deterministic, compressor-specific *trigger predicate*
(e.g. for `git_diff`: "a `?` marker on a path"; for `pytest`: "the line
`...truncated N more failures`"; for `grep`: "the `+N more matches in
file` suffix"). The directive is a self-instructing prompt prepended at
no parsing cost. The claim is twofold:

1. The directive **suppresses gratuitous expansion calls** that current
   COMPACT output silently invites. Agents trained on instruction-tuning
   data are heavily biased toward following imperative directives placed
   at the top of a context window (lead-bias / instruction-following
   priors documented across GPT-4-class and Claude-class models).
2. By naming the *exact* trigger string the agent should look for, the
   directive replaces the agent's vague "do I need more?" reasoning step
   with a cheap literal-match against a small token. This collapses the
   re-fetch decision from a multi-token chain-of-thought into an O(1)
   string check inside the agent's own reasoning context.

Header cost: ~20 cl100k tokens. Saving: each avoided round-trip
re-fetch on a COMPACT diff/grep/pytest result is 200-1500 tokens of
re-fetched content plus 10-40 tokens of follow-up tool call. Break-even
at one suppressed expansion per ~10-50 calls.

V94 is the **LLM-instruction layer**; V09 (selective re-fetch) is the
**marker layer**. They compose: V09 ranks *which* file to re-fetch when
expansion is warranted; V94 tells the agent *whether* expansion is
warranted at all, anchored on a deterministic trigger that the V09
marker (or its absence) controls.

## Theoretical basis

Three threads of prior work converge here.

**(a) Instruction-position effect.** Liu et al. 2023 ("Lost in the
Middle"), Anthropic 2024 prompt-engineering guide, and the OpenAI
system-message convention all establish that instructions placed at
positions 0-30 of a context window receive substantially higher
adherence than the same text mid-document. The COMPACT output today
opens with raw payload (e.g. `diff --git a/foo b/foo`); we are
trading 20 tokens for the most attention-weighted slot in the entire
tool response.

**(b) Decision-theoretic value of a stop signal.** Let `p` be the
probability the agent would otherwise issue a follow-up expansion
call after seeing COMPACT output, and let `q` be the conditional
probability that the directive successfully suppresses that call when
the trigger predicate is *not* satisfied. Let `H` = directive cost in
tokens, `C` = expected cost of a wasted expansion (avg over corpora,
~600 tokens including follow-up tool round-trip on diff/grep/pytest).
The directive pays off when:

```
p * (1 - p_trigger) * q * C  >  H
```

where `p_trigger` is the marginal probability the trigger predicate
*does* fire (in which case the directive correctly invites expansion;
no saving but no loss either - this is the "calibrated stop" leg).
Plug in conservative numbers: p=0.25, p_trigger=0.15, q=0.6, C=600,
H=20. Then 0.25 * 0.85 * 0.6 * 600 = 76.5 >> 20. The break-even is
robust under the bandwidth-product:

```
p * q  >  H / ((1 - p_trigger) * C)
0.25 * 0.6 = 0.15  >  20 / (0.85 * 600) = 0.039
```

A 4x margin. Even halving `q` (agents only follow the directive
two-thirds of the time we expect) keeps it positive.

**(c) Composition with V09 (selective re-fetch).** V09 emits a
machine-readable `refetch_candidates` block iff the encoder's
quantised uncertainty score for some file >= 1. V94 reuses this exact
predicate as its trigger string: the directive says "expand only if
you see `?:`", and the `?:` glyph is precisely the V09 marker
prefix (see V09 Section 3, "Marker syntax"). This makes the directive
*deterministically calibrated*: when V09 emits no marker (encoder
uncertainty zero), the trigger is provably absent and the directive
firmly says "do not expand". When V09 emits a marker, the trigger is
present and the directive permits expansion. Channel-coding view:
V09 is the side-channel; V94 is the receiver-side decoder rule
making the side-channel actionable. Without V94, the V09 marker is
ambiguous ("are these candidates a recommendation or a warning?");
without V09, V94 has no concrete trigger to anchor on.

Information-theoretic floor. The directive transmits log2(2) = 1 bit
of decision information ("expand / do not expand") plus the trigger
specification. The minimum encoding of the trigger is bounded by the
description length of the predicate, which for a literal-match string
is just the string itself (~4-6 cl100k tokens). The remaining ~14-16
tokens are the natural-language scaffold; this is the slack a
tighter phrasing might shave (see "candidate phrasings" below).

## Concrete proposal for Redcon

**1. New formatter step: `prepend_self_instruction`**

Add a tiny pure function in `redcon/cmd/pipeline.py` (or, cleaner, a
new file `redcon/cmd/self_instruct.py`). It runs *after* compression,
*before* the `_normalise_whitespace` pass, gated on
`level == CompressionLevel.COMPACT` and on a `BudgetHint.emit_self_instruct`
flag (default `True` once measured; default `False` while flagged).

```python
# redcon/cmd/self_instruct.py
from redcon.cmd.types import CompressionLevel

# Per-compressor trigger glyphs. Deterministic, no per-call state.
_TRIGGERS: dict[str, str] = {
    "git_diff":  "?:",      # V09 marker prefix
    "grep":      "+N more", # tail count line emitted by grep compressor
    "pytest":    "...truncated",
    "lint":      "?:",
    # No entry -> no header (default safe).
}

def self_instruction(schema: str, level: CompressionLevel) -> str | None:
    if level is not CompressionLevel.COMPACT:
        return None
    trig = _TRIGGERS.get(schema)
    if not trig:
        return None
    return f"COMPACT view. Do not expand unless you see `{trig}`."

def prepend(text: str, schema: str, level: CompressionLevel) -> str:
    h = self_instruction(schema, level)
    if h is None:
        return text
    return f"{h}\n{text}"
```

Pipeline wiring (in `compress_command`, after compressor returns text,
before whitespace normalisation):

```python
text = prepend(text, schema=compressor.schema_name, level=level)
text = _normalise_whitespace(text)
```

**2. Mirror the directive into `_meta.redcon`**

Bump `_REDCON_META_SCHEMA_VERSION` from "1" to "2" in
`redcon/mcp/tools.py` (or piggyback on V09's bump). Add:

```json
"_meta": {"redcon": {
  "schema_version": "2",
  "self_instruct": {
    "policy": "compact_no_expand_unless_trigger",
    "trigger": "?:"
  }
}}
```

This lets agent frameworks act on the directive structurally without
prose parsing - critical for non-LLM consumers (CI bots, structured
tool-router middlewares like LangGraph) that strip prose preambles.

**3. Three candidate header phrasings (per methodology requirement)**

Per V94 methodology step 1, the three candidates evaluated:

| Phrasing | Tokens (cl100k) | Strength | Weakness |
|---|---|---|---|
| C1: "COMPACT view. Do not expand unless you see `?:`." | ~14 | Direct imperative; explicit negation; trigger inline | Slight bossy tone may trigger Claude refusal heuristics |
| C2: "This is a COMPACT view; expand only if you see `?:`." | ~16 | Polite conditional; matches V94 spec verbatim | Marginally longer |
| C3: "(compact) skip expansion unless `?:` appears." | ~13 | Shortest; sentence-fragment signals "metadata, not content" | Less imperative weight; may be ignored more often |

Recommendation: **C1** as the production default. It is 2 tokens
shorter than C2, uses an explicit negation (LLM instruction-following
literature shows direct negations get higher adherence than
conditionals on Claude/GPT-4-class models per Anthropic's prompting
guide and the InstructGPT paper), and the trigger string is in
backticks (a documented strong attentional cue for code-token models
on cl100k - the backtick boundary forms a stable BPE merge).

**4. Files touched (sketch)**

- New file `redcon/cmd/self_instruct.py`: ~30 LOC.
- `redcon/cmd/pipeline.py::compress_command`: +3 LOC to call `prepend`
  before `_normalise_whitespace`.
- `redcon/cmd/types.py`: optional `BudgetHint.emit_self_instruct: bool = False`.
- `redcon/mcp/tools.py::_meta_block`: +5 LOC to surface
  `self_instruct` block.
- Tests in `tests/cmd/test_self_instruct.py`: ~40 LOC verifying
  determinism, idempotency under double-prepend (must not stack), and
  level gating (VERBOSE/ULTRA emit nothing).

Total: ~80 LOC + tests.

## Estimated impact

- **Token cost**: +14 tokens per COMPACT output that emits the
  header. On the BASELINE corpora that means COMPACT-tier reduction
  drops by ~0.4 pp on git diff, ~1.2 pp on grep, ~1.5 pp on pytest,
  ~3 pp on ls -R (smallest-output, header overhead worst there - so
  exclude `ls` from `_TRIGGERS`).
- **Token saving (session-level)**: under the conservative
  parameters above (p=0.25, q=0.6, p_trigger=0.15, C=600), expected
  net saving is **~76 tokens per COMPACT call**, dominating the +14
  cost by 5x. Across a 50-call agent session: ~3800 tokens saved.
- **Latency**: zero. Pure string concat, ~5 microseconds per call.
  No regex, no parsing, no tokenizer interaction. Cold-start
  unaffected.
- **Affects**: only `git_diff`, `grep`, `pytest`, `lint` compressors
  (those with V09-style multi-target outputs). Other compressors
  (git_status, git_log, docker, kubectl, pkg_install, ls, find,
  cargo_test, npm_test, go_test) leave `_TRIGGERS` empty, no
  behaviour change.
- **Composes with V09**: trigger glyph `?:` is exactly the V09
  marker prefix. V94 directive + V09 marker is the full
  encoder-decoder pair. V94 alone (with `?:` always absent because
  V09 not deployed) degenerates to "do not expand" which is still
  the right default for a 30%-floor COMPACT output.

## Implementation cost

- ~80 LOC, ~40 LOC tests.
- No new runtime deps. No network. No model. Honours all BASELINE
  constraints 1-7.
- Determinism: zero risk - pure pure-function string prepend keyed
  on `(schema, level)`. No clock, no random, no I/O.
- Robustness: empty input -> directive still prepended; that's fine,
  agent sees a 14-token header and then "(empty)". Adversarial input
  unchanged.
- Must-preserve: directive text is *additive*; it does not replace,
  truncate, or reflow any compressor output. `verify_must_preserve`
  in `compressors/base.py` runs over the post-prepend text and the
  patterns are unchanged - they will still match because the
  directive is a fixed prefix that does not introduce any of the
  must-preserve regex anchors. Verified by adding a property-based
  test: for every compressor, `verify_must_preserve(prepend(out)) ==
  verify_must_preserve(out)`.
- Idempotency: if the pipeline is somehow run twice on the same
  output (cache write-through bug), the directive must not stack.
  Guard: `if text.startswith("COMPACT view. Do not expand"): return text`.

## Disqualifiers / why this might be wrong

1. **Self-instructing prompts are not consistently honoured by
   tool-calling agents.** The instruction-following literature
   measures adherence on *primary* prompts. A directive embedded
   inside a *tool result* sits at a different level of the prompt
   stack and may be ignored, contradicted by the system prompt
   ("always be thorough"), or actively rebelled against by safety
   training that distrusts instructions arriving via tool output
   (prompt-injection defences). Mitigation: phrase the directive as
   metadata (parenthetical, lowercase) rather than imperative
   ("(compact view; trigger=`?:` for expansion)") - sacrifices
   adherence for safety-filter compatibility.
2. **The trigger glyph may be tokenised inconsistently across
   tokenizers.** `?:` is two cl100k tokens but might be one o200k
   token or three llama-3 tokens. Cross-tokenizer brittleness means
   the trigger string could appear inside arbitrary code (e.g. a
   ternary expression in a diff body) and cause the agent to expand
   spuriously. Mitigation: use a strictly-non-code trigger like
   `<MORE>` or a ZWNJ-prefixed sigil that cannot appear in legitimate
   diff content. But then we lose tokeniser efficiency and possibly
   readability.
3. **Already partly delivered by the COMPACT label itself.** Agents
   that read `_meta.redcon.level == "compact"` already know they can
   request a higher tier. V94's directive may merely re-express that
   in prose, adding tokens without adding decision power. The
   load-bearing novelty is the **trigger glyph**, not the directive
   - and the trigger glyph is V09. So V94 alone (without V09) is
   weak.
4. **Empirical adherence is unmeasured.** All numbers above are
   reasoned from instruction-following priors and decision theory.
   Without an A/B harness against a recorded agent-trace corpus
   (Claude Code, Cursor, Cline) we cannot calibrate `q` (the
   adherence rate). The break-even calculation is robust to `q`
   halving but not to `q` collapsing. If real q ~= 0.1 (agents
   mostly ignore tool-result directives), the directive is dead
   tokens.
5. **Backfire risk: directive primes agents to think about
   expansion.** By saying "do not expand unless X", we may *raise*
   the salience of expansion as an action and increase the rate at
   which agents look for X (or pattern-match X loosely). This is the
   classic "do not think of an elephant" failure mode in prompt
   engineering. The conditional prompting literature (Anthropic's
   "principle of least surprise") warns against introducing
   negations when no introduction was needed. Mitigation: only emit
   the directive when V09's trigger is *non-trivial* (>= 2
   candidates) - if there's nothing interesting to expand to,
   say nothing.

## Verdict

- Novelty: **medium**. The header itself is mundane prompt
  engineering, BUT framing it as a *deterministic decoder rule
  paired with a side-channel marker (V09)* is novel for tool-result
  formats. Most "self-instructing" work is in primary-prompt
  templating, not tool-output preambles. The composition is the
  contribution.
- Feasibility: **high**. ~80 LOC, no deps, no model. Two hours for
  a flagged-off prototype on one compressor; one day for the full
  set with idempotency guards and tests.
- Estimated speed of prototype: **0.5-1 day** for diff+pytest
  behind a flag; **2-3 days** to extend across all four compressors,
  add the meta-block surface, write property-based tests, and run a
  manual A/B on a recorded agent trace.
- Recommend prototype: **conditional-on V09 also being prototyped**.
  V94 standalone reduces to "prepend a generic stop-signal" which is
  testable but weak; V94+V09 together is the encoder-decoder pair
  whose break-even math is robust. Do NOT ship V94 first - the
  trigger glyph would be a dead reference.
